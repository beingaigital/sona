"""舆情数据采集工具：根据检索词、时间范围等参数抓取舆情数据。"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from langchain_core.tools import tool
from playwright.async_api import async_playwright, Page

from utils.env_loader import get_env_config
from utils.path import ensure_task_dirs
from utils.task_context import get_task_id


# 参数配置
@dataclass
class SearchConfig:
    """搜索配置"""
    keywords: List[str]  # 检索词列表
    time_range: str  # 时间范围
    group_name: str = "微信"  # 平台名称
    sort: str = "hot"  # relevance/hot
    page_size: int = 50
    info_type: str = "1"  # 1=全部 2=内容 3=评论
    
    # 平台特定筛选
    we_media_filter: str = "小红书;微头条;一点号;头条号;企鹅号;百家号;网易号;搜狐号;新浪号;大鱼号;人民号;快传号;澎湃号;大风号"
    video_filter: str = "抖音;快手;哔哩哔哩;今日头条;西瓜视频"
    forum_filter: str = "百度贴吧;知乎;豆瓣"

# 请求上下文
@dataclass
class RequestContext:
    """请求上下文"""
    headers: Dict[str, str]
    cookies: Dict[str, str]


# 基础参数（基础配置，通常无需修改）
BASE_PARAMS = {
    # 关键词匹配范围：0=标题；ALL=全文匹配（标题+正文+评论等）
    "keyWordIndex": "ALL",
    # 微博内容关键词匹配规则：2=原创
    "weiboWordIndex": "2",
    # 论坛内容关键词匹配规则：2=主帖
    "luntanWordIndex": "2",
    # 水军异常主标签过滤：ALL=包含所有异常标签（不过滤）
    "trollLabelFilter": "ALL",
    # 水军异常子标签过滤：多选IP/设备/行为/内容异常的账号内容
    "trollSubFilter": "IP地域异常;登录设备异常;发文行为异常;发文内容异常",
    # 结果排序方式：relevance=按相关度（支持翻页，可获取全部数据）/hot=按热度（仅返回前50条，不支持翻页）
    "sort": "hot",
    # 重点监控具体网站：留空=无限制
    "monitorSite": "",
    # 排除的具体网站：留空=无排除
    "excludeWeb": "",
    # 细分行业/领域：留空=无限制
    "industrySector": "",
    # 事件类型（平台预设）：留空=无限制
    "eventType": "",
    # 排除词匹配范围：0;1;2;3=标题/正文/评论/来源均过滤
    "excludeWordsIndex": "0;1;2;3",
    # 发布者IP属地：留空=无限制
    "ipLocation": "",
    # 账号认证属地：留空=无限制
    "signLocation": "",
    # 内容敏感倾向/主题：多选全量预设敏感分类
    "sensitivityTendency": "民生问题;环保问题;教育问题;医疗问题;自然灾害;腐败问题;事故灾难;热点事件;社会不公;社会安全;司法问题;民族分裂;暴恐问题;军警问题;信访维权;意识形态;宗教问题;其他",
    # 媒体/账号所属行业：多选全量行业分类
    "mediaIndustry": "娱乐;公益;广告;游戏;气象;民族与宗教;通信;能源;航空;政务;财经;医疗健康;科技;军事;教育;农林牧渔业;电商;体育;汽车;房产;旅游;文化;食品;其它",
    # 内容本身所属行业：与mediaIndustry一致，平台联动冗余配置
    "contentIndustry": "娱乐;公益;广告;游戏;气象;民族与宗教;通信;能源;航空;政务;财经;医疗健康;科技;军事;教育;农林牧渔业;电商;体育;汽车;房产;旅游;文化;食品;其它",
    # 内容提及的地域：留空=无限制
    "contentArea": "",
    # 媒体/账号所属地域：多选全国所有省市+港澳台+其他
    "mediaArea": "北京;天津;河北;山西;内蒙古;辽宁;吉林;黑龙江;上海;江苏;浙江;安徽;福建;江西;山东;河南;湖北;湖南;广东;广西;海南;重庆;四川;贵州;云南;西藏;陕西;甘肃;青海;宁夏;新疆;台湾;香港;澳门;其它",
    # 微博账号认证类型：多选全量认证类型
    "weiboFilter": "黄v;橙v;金v;蓝v;无认证",
    # 自媒体平台精准筛选：分号分隔多选（此处为"自媒体号"下全部勾选）
    "weMediaFilter": "小红书",
    # 视频平台精准筛选：分号分隔多选，按需增删
    "videoFilter": "抖音;快手;哔哩哔哩;今日头条;西瓜视频",
    # 论坛平台精准筛选：分号分隔多选，按需增删
    "forumFilter": "百度贴吧;知乎;豆瓣",
    # 去重规则：urlRemove=按URL去重（同一内容只保留一条）
    "simflag": "urlRemove",
    # 媒体级别：ALL=不限制（中央/省级/地方/自媒体等）
    "mediaLevel": "ALL",
    # 内容情感倾向：ALL=不限制（正面/负面/中性）
    "emotion": "ALL",
    # 内容发布方式：ALL=不限制（原创/转发/评论/置顶等）
    "sendWay": "ALL",
    # 内容类型：1=全部；2=内容；3=评论
    "infoType": "1",
    # 前端筛选器重载ID：平台前端缓存用，固定0即可
    "reloadFilterId": "0",
    # 前端页面重载ID：平台前端缓存用，固定0即可
    "reloadId": "0",
    # 预警方式：ALL=不限制（系统/人工预警等）
    "warnFangshi": "ALL",
    # 内容预警类型：ALL=不限制（敏感/风险/违法预警等）
    "hasAlertTypes": "ALL",
    # 前端更多状态控制：平台冗余配置，固定true即可
    "allList": "true",
    # 自定义筛选规则ID：留空=无自定义规则
    "ruleId": "",
    # 搜索类型：precise=精准搜索/fuzzy=模糊搜索
    "searchType": "precise",
    # 前端更多状态控制：平台冗余配置，固定false即可
    "moreStatus": "false",
    # 内容来源精准筛选：与groupName一致，平台联动冗余配置；ALL=不限制
    "source": "ALL",
    # 模糊搜索匹配范围：fullText=全文（此处为精准搜索，保留配置）
    "fuzzyValueScope": "fullText",
    # 模糊搜索补充关键词：留空=无补充（此处为精准搜索）
    "fuzzyValue": "",
    # 前端AbortSignal对象：平台前端控制用，固定值
    "signal": "[object AbortSignal]"
}

# 请求URL
API_URL = "https://pro.netinsight.com.cn/netInsight/general/advancedSearch/infoList"
LOGIN_URL = "https://pro.netinsight.com.cn/login"


async def _login_and_capture(
    username: str,
    password: str,
    keyword: str = "元宝派",
    headless: bool = True
) -> RequestContext:
    """使用 Playwright 登录并捕获请求凭证"""
    captured_request: Optional[Dict] = None
    
    async def handle_request(request):
        """请求拦截处理器"""
        nonlocal captured_request
        url = request.url
        target_url_pattern = 'netInsight/general/advancedSearch/infoList'
        if target_url_pattern in url:
            if captured_request is None:  # 只捕获第一个匹配的请求
                captured_request = {
                    'url': url,
                    'method': request.method,
                    'headers': dict(request.headers),
                    'post_data': request.post_data
                }
    
    async def handle_response(response):
        """响应监听器（用于调试）"""
        url = response.url
        target_url_pattern = 'netInsight/general/advancedSearch/infoList'
        if target_url_pattern in url:
            pass  # 可以在这里添加调试信息
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()
        
        # 在 context 级别注册监听器（可以捕获所有页面的请求）
        context.on('request', handle_request)
        context.on('response', handle_response)
        # 也在当前页面注册
        page.on('request', handle_request)
        page.on('response', handle_response)
        
        try:
            # 访问登录页面
            await page.goto(LOGIN_URL, wait_until='networkidle')
            await page.wait_for_timeout(2000)
            
            # 填写账号
            account_input = page.locator('input[placeholder="账号"]')
            await account_input.fill(username)
            
            # 填写密码
            password_input = page.locator('input[placeholder="密码"]')
            await password_input.fill(password)
            
            # 点击登录按钮
            login_button = page.locator('button.el-button--primary:has-text("登 录")')
            await login_button.click()
            
            # 等待登录完成
            await page.wait_for_timeout(3000)
            await page.wait_for_load_state('networkidle')
            await page.wait_for_timeout(5000)  # 增加等待时间
            
            # 再次确认页面完全稳定
            await page.wait_for_load_state('networkidle')
            await page.wait_for_timeout(3000)
            
            # 重置捕获状态（准备捕获搜索请求）
            captured_request = None
            
            # 查找关键词输入框（增加等待时间）
            await page.wait_for_timeout(2000)
            keyword_input = page.locator('input[name="plname"][placeholder="请输入搜索关键字"]')
            await keyword_input.wait_for(state='visible', timeout=15000)
            
            # 输入关键词（减慢速度）
            await page.wait_for_timeout(2000)
            await keyword_input.click()
            await page.wait_for_timeout(1000)
            await keyword_input.fill(keyword)
            await page.wait_for_timeout(2000)
            
            # 触发搜索（尝试多种方式）
            search_triggered = False
            
            # 方式1: 点击搜索图标
            try:
                search_icon = page.locator('input[name="plname"]').locator('xpath=ancestor::div[contains(@class, "el-input")]//svg[contains(@class, "icon-search")]').first
                await search_icon.wait_for(state='visible', timeout=3000)
                await search_icon.click()
                search_triggered = True
            except Exception:
                pass
            
            # 方式2: 如果点击失败，尝试按 Enter 键
            if not search_triggered:
                try:
                    await keyword_input.press('Enter')
                    search_triggered = True
                except Exception:
                    pass
            
            # 方式3: 如果都失败，尝试找到并点击搜索图标的父元素
            if not search_triggered:
                try:
                    search_parent = page.locator('span.el-input__suffix svg.icon-search').first
                    await search_parent.wait_for(state='visible', timeout=3000)
                    await search_parent.click()
                    search_triggered = True
                except Exception:
                    pass
            
            # 尝试等待新页面打开
            try:
                new_page = await context.wait_for_event('page', timeout=10000)
                # 在新页面注册监听器
                new_page.on('request', handle_request)
                await new_page.wait_for_timeout(3000)
                page = new_page
            except Exception:
                pass
            
            # 等待搜索请求被捕获
            max_wait_time = 10  # 最多等待10秒
            wait_interval = 0.5
            waited_time = 0
            
            while captured_request is None and waited_time < max_wait_time:
                await asyncio.sleep(wait_interval)
                waited_time += wait_interval
            
            if not captured_request:
                raise RuntimeError("未能捕获到搜索请求")
            
            # 获取 cookies 并提取需要的两个参数
            cookies_list = await context.cookies()
            cookies_dict = {cookie['name']: cookie['value'] for cookie in cookies_list}
            
            # 只提取需要的两个 cookies
            trs_session_id = cookies_dict.get('TRSJSESSIONID')
            trs_session_id_web = cookies_dict.get('TRSJSESSIONIDWEB')
            
            if not trs_session_id or not trs_session_id_web:
                raise RuntimeError(
                    f"未能获取到必要的 cookies。"
                    f"TRSJSESSIONID: {trs_session_id is not None}, "
                    f"TRSJSESSIONIDWEB: {trs_session_id_web is not None}"
                )
            
            # 只使用两个必要的 cookies
            final_cookies = {
                "TRSJSESSIONID": trs_session_id,
                "TRSJSESSIONIDWEB": trs_session_id_web
            }
            
            # 使用固定的请求头
            headers_dict = _build_headers(trs_session_id_web)
            
            return RequestContext(
                headers=headers_dict,
                cookies=final_cookies
            )
            
        finally:
            await browser.close()


def _build_headers(authorization: str) -> Dict[str, str]:
    """构建固定的请求头"""
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Authorization": authorization,  # 使用 TRSJSESSIONIDWEB 作为 Authorization
        "Connection": "keep-alive",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://pro.netinsight.com.cn",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "signal": "[object AbortSignal]"
    }


def _load_request_context(max_retries: int = 3) -> RequestContext:
    """登录并获取请求上下文"""
    # 从环境变量获取账号密码
    env = get_env_config()
    username = os.getenv("NETINSIGHT_USER") or env.NETINSIGHT_USER
    password = os.getenv("NETINSIGHT_PASS") or env.NETINSIGHT_PASS
    
    if not username or not password:
        raise ValueError(
            "未配置 NetInsight 登录信息。"
            "请设置环境变量 NETINSIGHT_USER 和 NETINSIGHT_PASS，"
            "或在 .env 文件中配置。"
        )
    
    # 运行异步登录函数，带重试机制
    headless = os.getenv("NETINSIGHT_HEADLESS", "true").lower() == "true"
    
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            context = asyncio.run(_login_and_capture(username, password, headless=headless))
            # 验证是否成功获取到 cookies
            if context.cookies.get('TRSJSESSIONID') and context.cookies.get('TRSJSESSIONIDWEB'):
                return context
            else:
                raise RuntimeError("未能获取到必要的 cookies")
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(2)  # 等待2秒后重试
            else:
                raise RuntimeError(f"登录失败（已重试 {max_retries} 次）: {str(e)}") from last_error
    
    raise RuntimeError(f"登录失败（已重试 {max_retries} 次）: {str(last_error)}") from last_error


def _build_payload(config: SearchConfig, page_no: int, page_id: Optional[str] = None, context: Optional[RequestContext] = None) -> Dict[str, str]:
    """构建请求体"""
    # 基础参数
    payload = BASE_PARAMS.copy()
    
    # 将检索词列表转换为API需要的格式
    keyword_str = ";".join(config.keywords) if isinstance(config.keywords, list) else str(config.keywords)
    
    # 应用核心配置（强制覆盖）
    payload.update({
        "keyWord": json.dumps({
            "wordSpace": None,
            "wordOrder": False,
            "keyWords": keyword_str
        }, ensure_ascii=False),
        "timeRange": config.time_range,
        "groupName": config.group_name,  # 使用指定的平台名称
        "source": config.group_name,
        "sort": config.sort,
        "pageSize": str(config.page_size),
        "pageNo": str(page_no),
        "infoType": config.info_type,
    })
    
    # 添加 pageId（翻页必需）
    if page_id:
        payload["pageId"] = page_id
    
    # 平台特定筛选（仅在需要时添加）
    if config.group_name != "ALL" and ";" not in config.group_name:
        # 单一平台，清理其他筛选器
        payload.pop("weMediaFilter", None)
        payload.pop("videoFilter", None)
        payload.pop("forumFilter", None)
        
        if config.group_name == "自媒体号":
            payload["weMediaFilter"] = config.we_media_filter
        elif config.group_name == "视频":
            payload["videoFilter"] = config.video_filter
        elif config.group_name == "论坛":
            payload["forumFilter"] = config.forum_filter
    
    return payload


def _fetch_page(
    config: SearchConfig,
    context: RequestContext,
    page_no: int,
    page_id: Optional[str] = None,
    max_retries: int = 3
) -> tuple[List[Dict], Optional[str], bool]:
    """获取单页数据"""
    payload = _build_payload(config, page_no, page_id, context)
    
    session = requests.Session()
    session.headers.update(context.headers)
    session.cookies.update(context.cookies)
    
    for attempt in range(1, max_retries + 1):
        try:
            response = session.post(
                API_URL,
                data=payload,
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            
            code = result.get("code")
            message = result.get("message")
            
            # code=204 表示没有符合条件的数据
            if code == 204:
                # 返回特殊标记，表示没有数据，需要在错误信息中提示调整检索词
                return [], None, True
            
            if code != 200:
                error_msg = f"API返回错误: code={code}, msg={message or '未知'}"
                if code == 515:
                    error_msg += " (登录态失效，请重新运行登录模块生成凭证)"
                raise RuntimeError(error_msg)
            
            data = result.get("data", {}) or {}
            content = data.get("content", {}) or {}
            items = content.get("pageItems", [])
            
            # 提取下一页需要的 pageId
            next_page_id = data.get("pageId") if page_no == 0 else page_id
            
            return items, next_page_id, False
            
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                time.sleep(2)
            else:
                raise
        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                time.sleep(2)
            else:
                raise
    
    return [], None, False


def _extract_main_fields(item: Dict) -> Dict[str, Any]:
    """从原始数据中提取主要字段"""
    # 定义主要字段映射
    main_fields = {
        "id": item.get("id", ""),
        "标题": item.get("title") or item.get("copyTitle", ""),
        "内容": item.get("content") or item.get("copyAbstracts", "") or item.get("abstracts", ""),
        "作者": item.get("author") or item.get("screenName", ""),
        "平台": item.get("channel") or item.get("groupName", ""),
        "发布时间": item.get("timeBak") or item.get("time", ""),
        "发布时间戳": item.get("time", ""),
        "URL": item.get("urlName", ""),
        "情感": item.get("emotion") or item.get("appraiseNew", ""),
        "评论数": item.get("commentNum", 0),
        "转发数": item.get("shareNum", 0),
        "点赞数": item.get("prNum", 0),
        "来源": item.get("siteName") or item.get("siteNameBak", ""),
        "IP属地": item.get("ipLocation", ""),
        "命中关键词": ";".join(item.get("keyWordes", [])) if isinstance(item.get("keyWordes"), list) else item.get("hitWord", ""),
        "行业类型": item.get("industryType", ""),
    }
    
    # 清理 HTML 标签（简单处理）
    for key in ["标题", "内容"]:
        if main_fields[key]:
            # 移除简单的 HTML 标签
            main_fields[key] = re.sub(r'<[^>]+>', '', str(main_fields[key]))
    
    return main_fields


def _clean_surrogate_chars(text: str) -> str:
    """清理代理对字符（surrogate pairs），这些字符无法用UTF-8编码"""
    if not isinstance(text, str):
        return text
    
    # 方法1：直接移除代理对字符（范围：U+D800 到 U+DFFF）
    # 这些字符是无效的Unicode字符，不应该出现在正常文本中
    cleaned = ''.join(
        char for char in text 
        if not ('\ud800' <= char <= '\udfff')
    )
    
    # 方法2：如果仍有编码问题，使用replace策略
    try:
        # 验证可以正常编码
        cleaned.encode('utf-8')
        return cleaned
    except (UnicodeEncodeError, UnicodeDecodeError):
        # 如果还有问题，使用replace策略
        return cleaned.encode('utf-8', errors='replace').decode('utf-8', errors='replace')


def _clean_value(value: Any) -> str:
    """清理值，确保可以安全地写入CSV"""
    if value is None:
        return ""
    elif isinstance(value, (dict, list)):
        # JSON序列化时也需要处理编码问题
        json_str = json.dumps(value, ensure_ascii=False)
        return _clean_surrogate_chars(json_str)
    elif isinstance(value, str):
        return _clean_surrogate_chars(value)
    else:
        # 转换为字符串后清理
        return _clean_surrogate_chars(str(value))


def _save_to_csv(items: List[Dict], output_path: Path) -> None:
    """保存数据到CSV文件，只提取主要信息"""
    if not items:
        return
    
    # 提取主要字段
    main_fields_list = [_extract_main_fields(item) for item in items]
    
    # 获取所有字段名
    fieldnames = list(main_fields_list[0].keys())
    
    # 清理字段名中的代理对字符
    fieldnames = [_clean_surrogate_chars(field) for field in fieldnames]
    
    with open(output_path, 'w', encoding='utf-8-sig', newline='', errors='replace') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        
        for row in main_fields_list:
            # 处理 None 值并清理代理对字符
            cleaned_row = {}
            for key, value in row.items():
                # 清理键名
                clean_key = _clean_surrogate_chars(key)
                # 清理值
                cleaned_row[clean_key] = _clean_value(value)
            writer.writerow(cleaned_row)


def _get_field_info(items: List[Dict]) -> Dict[str, str]:
    """分析字段信息（基于提取后的主要字段）"""
    if not items:
        return {}
    
    # 提取主要字段进行分析
    main_fields_list = [_extract_main_fields(item) for item in items]
    if not main_fields_list:
        return {}
    
    field_info = {}
    sample = main_fields_list[0]
    
    for key, value in sample.items():
        if isinstance(value, dict):
            field_info[key] = "JSON对象"
        elif isinstance(value, list):
            field_info[key] = "JSON数组"
        elif isinstance(value, bool):
            field_info[key] = "布尔值"
        elif isinstance(value, int):
            field_info[key] = "整数"
        elif isinstance(value, float):
            field_info[key] = "浮点数"
        elif isinstance(value, str):
            field_info[key] = "字符串"
        else:
            field_info[key] = str(type(value).__name__)
    
    return field_info


def _get_field_descriptions() -> Dict[str, str]:
    """获取字段说明"""
    return {
        "id": "数据唯一标识符，每条舆情数据的唯一ID",
        "标题": "舆情内容的标题",
        "内容": "舆情内容的正文",
        "作者": "发布该舆情内容的作者名称或账号",
        "平台": "舆情内容发布的平台，如：微博、微信、新闻网站等",
        "发布时间": "舆情内容的发布时间，格式为：YYYY-MM-DD HH:MM:SS",
        "发布时间戳": "舆情内容的发布时间戳（Unix时间戳，毫秒）",
        "URL": "舆情内容的原始链接地址",
        "情感": "舆情内容的情感倾向，如：正面、负面、中性",
        "评论数": "该舆情内容的评论数量",
        "转发数": "该舆情内容的转发/分享数量",
        "点赞数": "该舆情内容的点赞/喜欢数量",
        "来源": "舆情内容的来源网站或媒体名称",
        "IP属地": "发布该舆情内容的IP地址所属地区",
        "命中关键词": "在该舆情内容中命中的检索关键词，多个关键词用分号分隔",
        "行业类型": "舆情内容所属的行业分类，如：财经、科技、娱乐等"
    }


@tool
def data_collect(
    searchMatrix: str,
    timeRange: str
) -> str:
    """
    描述：根据搜索矩阵和时间范围循环抓取微博渠道的舆情数据。根据提供的搜索矩阵（包含多个搜索词及其对应的数量），循环爬取每个搜索词的数据，最终汇总到一个CSV文件并按照内容列进行去重。
    使用时机：当需要根据搜索矩阵批量抓取微博舆情数据时调用本工具。搜索矩阵通常由 data_num 工具生成。
    输入：
    - searchMatrix（必填）：搜索矩阵，JSON字符串格式，例如 '{"关键词1": 数量1, "关键词2": 数量2, "关键词3": 数量3}'。每个关键词对应需要爬取的数量。
    - timeRange（必填）：搜索时间范围，格式如 "2026-01-01 00:00:00;2026-01-31 23:59:59"。
    输出：JSON字符串，包含以下字段：
    - save_path：CSV文件保存路径（汇总后的最终文件）
    - meta：元数据信息
      - platform：爬取的平台名称（固定为"微博"）
      - count：实际爬取的数量（去重后）
      - fields：包含的字段列表
      - field_types：字段类型说明
      - search_summary：每个搜索词的爬取情况摘要
    """
    import json as json_module
    
    # 解析搜索矩阵
    try:
        if isinstance(searchMatrix, str):
            try:
                matrix = json_module.loads(searchMatrix)
                if not isinstance(matrix, dict):
                    return json_module.dumps({
                        "error": "搜索矩阵必须是字典格式，例如 '{\"关键词1\": 数量1, \"关键词2\": 数量2}'",
                        "save_path": "",
                        "meta": {}
                    }, ensure_ascii=False)
            except json_module.JSONDecodeError:
                return json_module.dumps({
                    "error": "搜索矩阵格式错误，必须是有效的JSON字符串",
                    "save_path": "",
                    "meta": {}
                }, ensure_ascii=False)
        elif isinstance(searchMatrix, dict):
            matrix = searchMatrix
        else:
            return json_module.dumps({
                "error": "搜索矩阵格式错误",
                "save_path": "",
                "meta": {}
            }, ensure_ascii=False)
    except Exception as e:
        return json_module.dumps({
            "error": f"解析搜索矩阵失败: {str(e)}",
            "save_path": "",
            "meta": {}
        }, ensure_ascii=False)
    
    if not matrix:
        return json_module.dumps({
            "error": "搜索矩阵不能为空",
            "save_path": "",
            "meta": {}
        }, ensure_ascii=False)
    
    # 验证搜索矩阵格式：每个值应该是整数
    for keyword, count in matrix.items():
        if not isinstance(count, int) or count <= 0:
            return json_module.dumps({
                "error": f"搜索词 '{keyword}' 的数量必须是大于0的整数",
                "save_path": "",
                "meta": {}
            }, ensure_ascii=False)
    
    # 获取任务ID
    task_id = get_task_id()
    if not task_id:
        return json_module.dumps({
            "error": "未找到任务ID，请确保在Agent上下文中调用",
            "save_path": "",
            "meta": {}
        }, ensure_ascii=False)
    
    # 确保任务目录存在
    process_dir = ensure_task_dirs(task_id)
    
    # 登录并获取凭证
    try:
        context = _load_request_context()
    except ValueError as e:
        return json_module.dumps({
            "error": str(e),
            "save_path": "",
            "meta": {}
        }, ensure_ascii=False)
    except Exception as e:
        return json_module.dumps({
            "error": f"登录失败: {str(e)}",
            "save_path": "",
            "meta": {}
        }, ensure_ascii=False)
    
    # 固定平台为微博
    group_name = "微博"
    
    # 循环爬取每个搜索词的数据
    all_items = []
    search_summary = {}
    
    try:
        for keyword, target_count in matrix.items():
            # 创建配置
            config = SearchConfig(
                keywords=[keyword],  # 单个关键词
                time_range=timeRange,
                group_name=group_name,
                sort="-commtCount",  # 按评论数降序排序（支持翻页）
                page_size=50,
                info_type="2"  # 2=内容（不是全部）
            )
            
            # 计算需要抓取的页数
            max_pages = (target_count + config.page_size - 1) // config.page_size
            
            # 抓取当前关键词的数据
            keyword_items = []
            page_id = None
            no_data_flag = False
            
            for page_no in range(max_pages):
                items, page_id, is_no_data = _fetch_page(config, context, page_no, page_id)
                
                # 检查是否是没有数据的标记
                if is_no_data:
                    no_data_flag = True
                    break
                
                if not items:
                    break
                
                keyword_items.extend(items)
                
                # 如果已达到目标数量，停止抓取
                if len(keyword_items) >= target_count:
                    keyword_items = keyword_items[:target_count]
                    break
                
                # 如果本页数据少于page_size，说明已到最后一页
                if len(items) < config.page_size:
                    break
                
                # 延迟避免风控
                if page_no < max_pages - 1:
                    time.sleep(2.0)
            
            # 记录每个关键词的爬取情况
            actual_count = len(keyword_items)
            search_summary[keyword] = {
                "target": target_count,
                "actual": actual_count,
                "status": "no_data" if no_data_flag and actual_count == 0 else "success" if actual_count > 0 else "failed"
            }
            
            # 添加到总数据列表
            all_items.extend(keyword_items)
            
            # 关键词之间的延迟
            if keyword != list(matrix.keys())[-1]:  # 不是最后一个关键词
                time.sleep(1.0)
    
    except Exception as e:
        return json_module.dumps({
            "error": f"数据抓取失败: {str(e)}",
            "save_path": "",
            "meta": {}
        }, ensure_ascii=False)
    
    # 去重：按照"内容"列进行去重
    if all_items:
        # 提取主要字段
        main_fields_list = [_extract_main_fields(item) for item in all_items]
        
        # 按照"内容"列去重（保留第一次出现的记录）
        seen_contents = set()
        deduplicated_items = []
        deduplicated_main_fields = []
        
        for item, main_fields in zip(all_items, main_fields_list):
            content = main_fields.get("内容", "").strip()
            # 如果内容为空，也保留（可能是标题等）
            if not content or content not in seen_contents:
                seen_contents.add(content)
                deduplicated_items.append(item)
                deduplicated_main_fields.append(main_fields)
        
        all_items = deduplicated_items
    else:
        deduplicated_main_fields = []
    
    # 保存到CSV
    if not all_items:
        error_msg = "未抓取到任何数据"
        if any(summary.get("status") == "no_data" for summary in search_summary.values()):
            error_msg += "。建议：1) 尝试调整检索词，使用更宽泛或相近的关键词；2) 扩大时间范围；3) 尝试使用同义词或相关词汇"
        
        return json_module.dumps({
            "error": error_msg,
            "save_path": "",
            "meta": {
                "platform": group_name,
                "count": 0,
                "fields": [],
                "field_types": {},
                "field_descriptions": _get_field_descriptions(),
                "search_summary": search_summary
            }
        }, ensure_ascii=False)
    
    # 生成文件名（包含时间戳）
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"netinsight_{group_name}_汇总_{timestamp}.csv"
    output_path = process_dir / filename
    
    try:
        _save_to_csv(all_items, output_path)
    except Exception as e:
        return json_module.dumps({
            "error": f"保存文件失败: {str(e)}",
            "save_path": "",
            "meta": {}
        }, ensure_ascii=False)
    
    # 分析字段信息
    field_info = _get_field_info(all_items)
    
    # 构建返回结果
    result = {
        "save_path": str(output_path),
        "meta": {
            "platform": group_name,
            "count": len(all_items),
            "fields": list(field_info.keys()),
            "field_types": field_info,
            "field_descriptions": _get_field_descriptions(),
            "search_summary": search_summary
        }
    }
    
    return json_module.dumps(result, ensure_ascii=False)
