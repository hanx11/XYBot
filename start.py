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
import sseclient
from loguru import logger

import xybot
from plans_manager import plan_manager
from plugin_manager import plugin_manager


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


async def with_requests(url, params, headers):
    """Get a streaming response for the given event feed using requests."""
    return requests.get(url, params=params, stream=True, headers=headers)


async def create_chat_gpt_dialog(message):
    url = "https://sg-api-ai.jiyinglobal.com/v1/m/gpt/chat/5445436cd51e11eeb8be4f8b59949224/completion/"
    headers = {
        "Token": "pbkdf2_sha256$600000$HBFQO0Rgb4gy8pzD4srHN4$dAnsp/B8TSiRiO74eVb5eVye/DNNZDtBXP/KcpmvLa8=",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Userid": '73',
    }
    data = {"messages": [{"type": "text", "text": message}]}
    try:
        resp = requests.post(url, json=data, headers=headers)
        if resp.status_code == 200:
            r_json = resp.json()
            logger.info(f"r_json: {r_json}")
            if r_json.get("message") == "success":
                url = "https://sg-api-ai.jiyinglobal.com/v1/m/gpt/chat/5445436cd51e11eeb8be4f8b59949224/completion/"
                params = {"pk": r_json['result']['pk']}
                response = await with_requests(url, params, headers)
                client = sseclient.SSEClient(response)
                resp_msg = b""
                for event in client.events():
                    logger.info(f"event: {event}")
                return message

        return "服务器开小差了，请稍后再试^_^"
    except Exception as exc:
        logger.error(f"create_chat_gpt_dialog_fail: {exc}")
        return "服务器开小差了，请稍后再试^_^"


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
                        if isinstance(recv['content'], str):  # 判断是否为txt消息
                            resp_msg = await create_chat_gpt_dialog(recv['content'])
                            bot.send_txt_msg(recv['wxid'], resp_msg)
                            # asyncio.create_task(message_handler(recv, handlebot)).add_done_callback(callback)
                except Exception as error:
                    logger.error('出现错误: {error}'.format(error=error))


if __name__ == "__main__":
    asyncio.run(main())
