"""Optional Chromium interaction checks, strictly using the offline FakeClient.
Run: python tests/browser_smoke.py
"""
import json
import os
import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
from test_webui import FakeClient
from webui import create_app

root = Path(__file__).resolve().parents[1]
app = create_app(FakeClient)
server = make_server('127.0.0.1', 0, app, threaded=True)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
url = f'http://127.0.0.1:{server.server_port}'
checks=[]
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        desktop=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
        errors=[]
        desktop.on('pageerror',lambda error:errors.append(str(error)))
        desktop.goto(url)
        desktop.get_by_role('button',name='发送验证码').wait_for(state='visible')
        desktop.wait_for_function("!document.getElementById('send-button').disabled")
        desktop.screenshot(path=str(root/'docs'/'webui-desktop.png'),full_page=True)
        assert desktop.evaluate('document.documentElement.scrollWidth<=innerWidth')
        checks.append('desktop layout, initial screen and no horizontal overflow')
        desktop.get_by_label('邮箱地址').fill('person@example.com')
        desktop.get_by_role('button',name='发送验证码').click()
        desktop.get_by_label('邮箱验证码').wait_for(state='visible')
        assert desktop.get_by_label('邮箱地址').is_disabled()
        desktop.reload()
        desktop.get_by_label('邮箱验证码').wait_for(state='visible')
        assert desktop.get_by_label('邮箱地址').input_value()=='person@example.com'
        desktop.get_by_label('邮箱验证码').fill('123456')
        desktop.get_by_role('button',name='验证邮箱',exact=True).click()
        desktop.get_by_role('heading',name='认识真实的你').wait_for()
        desktop.get_by_label('姓',exact=True).fill('测试')
        desktop.get_by_label('名',exact=True).fill('用户')
        desktop.get_by_label('出生日期').fill('1995-06-18')
        desktop.get_by_role('button',name='提交资料并注册').click()
        desktop.get_by_role('button',name='接受条款并激活会话').click()
        desktop.get_by_role('button',name='准备兑换会话').click()
        desktop.get_by_label('邀请码',exact=True).fill('MOCK-ONLY')
        desktop.get_by_role('button',name='兑换邀请码',exact=True).click()
        desktop.get_by_role('heading',name='兑换成功',exact=True).wait_for()
        desktop.get_by_role('button',name='获取我的邀请码').click()
        desktop.wait_for_function("document.getElementById('own-code').textContent==='TEST-INVITE'")
        checks.append('mocked full registration, activation, VM preparation, redemption and own code')
        desktop.get_by_role('button',name='清除会话',exact=True).click()
        desktop.get_by_role('button',name='保留会话').click()
        assert desktop.get_by_role('heading',name='兑换成功',exact=True).is_visible()
        desktop.get_by_role('button',name='清除会话',exact=True).click()
        desktop.locator('#confirm-reset').click()
        desktop.get_by_role('heading',name='从你的邮箱开始').wait_for()
        assert desktop.get_by_label('邮箱地址').input_value()==''
        checks.append('reset dialog cancel/confirm and clear fields')
        mobile=browser.new_page(viewport={'width':390,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
        mobile.on('pageerror',lambda error:errors.append(str(error)))
        mobile.goto(url)
        mobile.wait_for_function("!document.getElementById('send-button').disabled")
        mobile.screenshot(path=str(root/'docs'/'webui-mobile.png'),full_page=True)
        assert mobile.evaluate('document.documentElement.scrollWidth<=innerWidth')
        mobile.get_by_label('邮箱地址').fill('mobile@example.com')
        mobile.get_by_role('button',name='发送验证码').click()
        mobile.get_by_label('邮箱验证码').fill('123456')
        mobile.get_by_role('button',name='验证邮箱',exact=True).click()
        mobile.get_by_role('heading',name='认识真实的你').wait_for()
        assert mobile.evaluate('document.documentElement.scrollWidth<=innerWidth')
        checks.append('390px mobile layout and first two steps')
        assert errors==[],errors
        checks.append('zero browser JavaScript errors')
        browser.close()
    print(json.dumps({'checks':checks,'live_requests':False},ensure_ascii=False,indent=2))
finally:
    server.shutdown()
