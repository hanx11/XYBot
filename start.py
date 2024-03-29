import asyncio
import concurrent.futures
import json
import os
import pywxdll
import requests
import schedule
import time
import websockets
import yaml
from loguru import logger

import xybot
import redis
from plans_manager import plan_manager
from plugin_manager import plugin_manager

session = requests.session()

rds = redis.Redis()

headers = {
    "Token": "pbkdf2_sha256$600000$HBFQO0Rgb4gy8pzD4srHN4$dAnsp/B8TSiRiO74eVb5eVye/DNNZDtBXP/KcpmvLa8=",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Userid": '73',
}

WHITE_WX_IDS = [
    "hanfeng_1991",
    "wxid_jsuq7eo9gfu921",    # Sean
    "ppwspp",        # 彭伟
    "a27004317",     # 袁玲丽
]


async def message_handler(recv, handlebot):  # 处理收到的消息
    await asyncio.create_task(handlebot.message_handler(recv))


def callback(worker):  # 处理线程结束时，有无错误
    worker_exception = worker.exception()
    if worker_exception:
        logger.error(worker_exception)


async def plan_run_pending():  # 计划等待判定线程
    while True:
        schedule.run_pending()
        await asyncio.sleep(1)


async def get_random_sentence():
    return requests.get("https://v1.hitokoto.cn/").json()


async def gen_chp():
    return requests.get("https://api.shadiao.pro/chp").json()


async def with_requests(url, headers):
    """Get a streaming response for the given event feed using requests."""
    return requests.get(url, stream=True, headers=headers)


async def get_chat_id():
    cache_key = "chat_id"
    chat_id = rds.get(cache_key)
    if not chat_id:
        url = "https://sg-api-ai.jiyinglobal.com/v1/m/gpt/chat/"
        params = {"model": "gpt-4-1106-preview"}
        resp = requests.post(url, json=params, headers=headers, timeout=3)
        if resp.status_code == 200:
            r = resp.json()
            logger.info(f"{r}")
            chat_id = r['result']['id']
            rds.set(cache_key, chat_id)
            rds.expire(cache_key, 3600)
    return chat_id


async def create_chat_gpt_dialog(message):
    try:
        chat_id = await get_chat_id()
        if isinstance(chat_id, bytes):
            chat_id = chat_id.decode("utf-8")
        url = f"https://sg-api-ai.jiyinglobal.com/v1/m/gpt/chat/{chat_id}/completion/"
        data = {"messages": [{"type": "text", "text": message}]}
        logger.info(f"{url}")
        resp = requests.post(url, json=data, headers=headers, timeout=5)
        if resp.status_code == 200:
            r_json = resp.json()
            logger.info(f"{r_json}")
            if r_json.get("message") == "success":
                pk = r_json['result']['pk']
                request = requests.Request(method='GET', url=f"{url}?pk={pk}", headers=headers).prepare()
                content = ""
                while True:
                    r = session.send(request, timeout=3).json()
                    logger.info(f"{r}")
                    if r.get("code") != "0" or (r['result']['status'] == 0 and not r['result']['content']):
                        break

                    content += r['result']['content']
                    await asyncio.sleep(r['result']['wait'] / 1000)
                return content

        return "机器人去充电啦，请稍后再试^_^"
    except Exception as exc:
        logger.error(f"create_chat_gpt_dialog_fail: {exc}")
        return "机器人去充电啦，请稍后再试^_^"


async def chat_with_qwen(message):
    url = "http://172.17.0.3:11434/api/chat"
    data = {
        "model": "qwen:7b",
        "messages": [
            {"role": "user", "content": message}
        ]
    }
    resp_content = b""
    try:
        response = requests.post(url=url, json=data, stream=True)
        for msg in response.iter_lines():
            msg_type = type(msg)
            logger.info(f"{msg}, {msg_type}")
            msg = json.loads(msg)
            resp_content += msg["message"]["content"]
    except Exception as exc:
        print(exc)
        resp_content += "机器人去充电啦，请稍后再试^_^"
    finally:
        return resp_content


async def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # ---- log设置 读取设置 ---- #
    logger.add('logs/log_{time}.log', encoding='utf-8', enqueue=True, retention='2 weeks', rotation='00:01')  # 日志设置

    with open('main_config.yml', 'r', encoding='utf-8') as f:  # 读取设置
        config = yaml.safe_load(f.read())

    ip = config['ip']
    port = config['port']

    max_worker = config['max_worker']

    # ---- 机器人实例化 登陆监测 机器人启动 ---- #

    bot = pywxdll.Pywxdll(ip, port)

    # 检查是否登陆了微信
    logged_in = False
    while not logged_in:
        try:
            if bot.get_personal_detail('filehelper'):
                logged_in = True
                logger.success('机器人微信账号已登录！')
        except:
            logger.warning('机器人微信账号未登录！请使用浏览器访问 http://{ip}:4000/vnc.html 扫码登陆微信'.format(ip=ip))
            time.sleep(3)

    bot.start()  # 开启机器人

    handlebot = xybot.XYBot()

    # ---- 加载插件 加载计划 ---- #

    # 加载所有插件
    plugin_dir = "plugins"  # 插件目录的路径
    plugin_manager.load_plugins(plugin_dir)  # 加载所有插件

    plans_dir = "plans"
    plan_manager.load_plans(plans_dir)  # 加载所有计划

    asyncio.create_task(plan_run_pending()).add_done_callback(callback)  # 开启计划等待判定线程

    # ---- 进入获取聊天信息并处理循环 ---- #
    async with websockets.connect('ws://{ip}:{port}'.format(ip=ip, port=port)) as websocket:
        logger.success('机器人启动成功！')
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_worker):
            while True:
                try:
                    recv = json.loads(await websocket.recv())
                    r_type = recv['type']
                    if r_type == 1 or r_type == 3 or r_type == 49:
                        logger.info('[收到消息]:{message}'.format(message=recv))
                        if isinstance(recv['content'], str):    # 判断是否为txt消息
                            # asyncio.create_task(message_handler(recv, handlebot)).add_done_callback(callback)
                            if recv['wxid'] in WHITE_WX_IDS or recv['content'].startswith('@Walter'):
                                # resp_msg = await create_chat_gpt_dialog(recv['content'])
                                resp_msg = await chat_with_qwen(recv['content'])
                                r = bot.send_txt_msg(recv['wxid'], resp_msg)
                                logger.info(f"{r}")
                except Exception as error:
                    logger.error('出现错误: {error}'.format(error=error))


if __name__ == "__main__":
    asyncio.run(main())
