"""舆情数据数量查询工具：根据检索词查询微博渠道的数据数量。"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from langchain_core.tools import tool

from utils.env_loader import get_env_config

# 请求URL
COUNT_API_URL = "https://pro.netinsight.com.cn/netInsight/general/advancedSearch/infoCount"
LOGIN_URL = "https://pro.netinsight.com.cn/login"

SUPPORTED_PLATFORMS: List[str] = [
    "新闻网站",
    "新闻app",
    "视频",
    "微博",
    "微信",
    "自媒体号",
    "论坛",
    "电子报",
    "境外新闻",
    "Twitter",
    "Facebook",
]

PLATFORM_ALIASES: Dict[str, str] = {
    "新闻网站": "新闻网站",
    "新闻站点": "新闻网站",
    "新闻网": "新闻网站",
    "新闻app": "新闻app",
    "新闻APP": "新闻app",
    "新闻客户端": "新闻app",
    "视频": "视频",
    "微博": "微博",
    "微信": "微信",
    "自媒体号": "自媒体号",
    "自媒体": "自媒体号",
    "论坛": "论坛",
    "电子报": "电子报",
    "境外新闻": "境外新闻",
    "twitter": "Twitter",
    "Twitter": "Twitter",
    "x": "Twitter",
    "X": "Twitter",
    "facebook": "Facebook",
    "Facebook": "Facebook",
    "all": "ALL",
    "ALL": "ALL",
}

# 基础参数（用于数量查询）
BASE_COUNT_PARAMS = {
    # 关键词匹配范围：ALL=全文匹配（标题+正文+评论等）
    "keyWordIndex": "ALL",
    # 微博内容关键词匹配规则：1=全部（原创+转发）
    "weiboWordIndex": "1",
    # 论坛内容关键词匹配规则：1=全部
    "luntanWordIndex": "1",
    # 水军异常主标签过滤：ALL=包含所有异常标签（不过滤）
    "trollLabelFilter": "ALL",
    # 水军异常子标签过滤：多选IP/设备/行为/内容异常的账号内容
    "trollSubFilter": "IP地域异常;登录设备异常;发文行为异常;发文内容异常",
    # 结果排序方式：relevance=按相关度
    "sort": "relevance",
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
    # 自媒体平台精准筛选：分号分隔多选
    "weMediaFilter": "小红书;微头条;一点号;头条号;企鹅号;百家号;网易号;搜狐号;新浪号;大鱼号;人民号;快传号;澎湃号;大风号",
    # 视频平台精准筛选：分号分隔多选
    "videoFilter": "抖音;快手;哔哩哔哩;今日头条;西瓜视频;度小视;好看视频;微视;美拍;梨视频;电视视频;其他;百度视频",
    # 论坛平台精准筛选：分号分隔多选
    "forumFilter": "百度贴吧;知乎;豆瓣;其他",
    # 去重规则：urlRemove=按URL去重（同一内容只保留一条）
    "simflag": "urlRemove",
    # 媒体级别：ALL=不限制（中央/省级/地方/自媒体等）
    "mediaLevel": "ALL",
    # 内容情感倾向：ALL=不限制（正面/负面/中性）
    "emotion": "ALL",
    # 内容发布方式：ALL=不限制（原创/转发/评论/置顶等）
    "sendWay": "ALL",
    # 内容类型：1=全部
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
    # 搜索类型：fulltext=全文搜索
    "searchType": "fulltext",
    # 前端更多状态控制：平台冗余配置，固定false即可
    "moreStatus": "false",
    # 内容来源精准筛选：ALL=不限制
    "source": "ALL",
    # 页码：固定0（数量查询只需第一页）
    "pageNo": "0",
    # 每页返回条数：固定50（数量查询不需要实际数据）
    "pageSize": "50",
    # 前端AbortSignal对象：平台前端控制用，固定值
    "signal": "[object AbortSignal]"
}


def _should_bypass_netinsight_proxy() -> bool:
    v = os.environ.get("SONA_NETINSIGHT_NO_PROXY", "false").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _normalize_platform_name(platform: str) -> Optional[str]:
    """将平台参数归一化为标准平台名；支持 ALL。"""
    platform_text = (platform or "").strip()
    if not platform_text:
        return None
    return PLATFORM_ALIASES.get(platform_text)


async def _login_and_capture(
    username: str,
    password: str,
    keyword: str = "元宝派",
    headless: bool = True
) -> Dict[str, str]:
    """使用 Playwright 登录并捕获请求凭证"""
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        # 检查是否需要代理
        launch_options = {"headless": headless}
        bypass_proxy = _should_bypass_netinsight_proxy()

        if bypass_proxy:
            launch_options["args"] = ["--no-proxy-server"]
        else:
            # 尝试从环境变量获取代理
            proxy_url = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("ALL_PROXY")
            if not proxy_url:
                # 尝试从 .env 文件读取
                from dotenv import load_dotenv
                load_dotenv()
                proxy_url = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("ALL_PROXY")

            if proxy_url:
                # 解析代理地址
                proxy_config = {}
                if proxy_url.startswith("http://"):
                    proxy_config["server"] = proxy_url
                elif proxy_url.startswith("socks5://"):
                    proxy_config["server"] = proxy_url
                    proxy_config["type"] = "socks5"
                else:
                    proxy_config["server"] = f"http://{proxy_url.replace('http://', '').replace('https://', '')}"

                launch_options["proxy"] = proxy_config
                print(f"[data_num] Using proxy: {proxy_config}")
        
        browser = await p.chromium.launch(**launch_options)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            login_timeout_ms = max(10000, int(os.getenv("SONA_NETINSIGHT_LOGIN_TIMEOUT_MS", "90000")))
        except Exception:
            login_timeout_ms = 90000
        
        try:
            # 访问登录页：优先等 DOM 完成，避免 networkidle 卡死导致 30s 超时
            await page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=login_timeout_ms)
            await page.wait_for_timeout(4000)
            try:
                await page.wait_for_load_state('networkidle', timeout=min(15000, login_timeout_ms))
            except Exception:
                # 网络空闲等待失败不致命，继续走登录流程
                pass
            
            # 填写账号
            account_input = page.locator('input[placeholder="账号"]')
            await account_input.wait_for(state='visible', timeout=min(15000, login_timeout_ms))
            await account_input.fill(username)
            
            # 填写密码
            password_input = page.locator('input[placeholder="密码"]')
            await password_input.wait_for(state='visible', timeout=min(15000, login_timeout_ms))
            await password_input.fill(password)
            
            # 点击登录按钮
            login_button = page.locator('button.el-button--primary:has-text("登 录")')
            await login_button.click()
            
            # 等待登录完成
            await page.wait_for_timeout(3000)
            try:
                await page.wait_for_load_state('networkidle', timeout=min(20000, login_timeout_ms))
            except Exception:
                pass
            await page.wait_for_timeout(5000)
            
            # 再次确认页面完全稳定
            try:
                await page.wait_for_load_state('networkidle', timeout=min(15000, login_timeout_ms))
            except Exception:
                pass
            await page.wait_for_timeout(3000)
            
            # 获取 cookies
            cookies_list = await context.cookies()
            cookies_dict = {cookie['name']: cookie['value'] for cookie in cookies_list}
            
            # 提取需要的两个 cookies
            trs_session_id = cookies_dict.get('TRSJSESSIONID')
            trs_session_id_web = cookies_dict.get('TRSJSESSIONIDWEB')
            
            if not trs_session_id or not trs_session_id_web:
                raise RuntimeError(
                    f"未能获取到必要的 cookies。"
                    f"TRSJSESSIONID: {trs_session_id is not None}, "
                    f"TRSJSESSIONIDWEB: {trs_session_id_web is not None}"
                )
            
            return {
                "TRSJSESSIONID": trs_session_id,
                "TRSJSESSIONIDWEB": trs_session_id_web
            }
            
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


def _load_request_context(max_retries: int = 3) -> tuple[Dict[str, str], Dict[str, str]]:
    """登录并获取请求上下文，返回 (headers, cookies)"""
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
            cookies = asyncio.run(_login_and_capture(username, password, headless=headless))
            # 验证是否成功获取到 cookies
            if cookies.get('TRSJSESSIONID') and cookies.get('TRSJSESSIONIDWEB'):
                headers = _build_headers(cookies['TRSJSESSIONIDWEB'])
                return headers, cookies
            else:
                raise RuntimeError("未能获取到必要的 cookies")
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(2)  # 等待2秒后重试
            else:
                raise RuntimeError(f"登录失败（已重试 {max_retries} 次）: {str(e)}") from last_error
    
    raise RuntimeError(f"登录失败（已重试 {max_retries} 次）: {str(last_error)}") from last_error


def _query_weibo_count(
    keyword: str,
    time_range: str,
    headers: Dict[str, str],
    cookies: Dict[str, str],
    platform_name: str,
    max_retries: int = 3
) -> int:
    """查询单个关键词在指定平台下的数量"""
    # 构建请求参数
    payload = BASE_COUNT_PARAMS.copy()
    payload.update({
        "keyWord": json.dumps({
            "wordSpace": None,
            "wordOrder": False,
            "keyWords": keyword
        }, ensure_ascii=False),
        "timeRange": time_range,
        "groupName": "ALL",  # 查询所有平台
    })
    
    session = requests.Session()
    if _should_bypass_netinsight_proxy():
        session.trust_env = False
    session.headers.update(headers)
    session.cookies.update(cookies)
    
    for attempt in range(1, max_retries + 1):
        try:
            response = session.post(
                COUNT_API_URL,
                data=payload,
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            
            code = result.get("code")
            if code != 200:
                error_msg = f"API返回错误: code={code}, msg={result.get('message', '未知')}"
                raise RuntimeError(error_msg)
            
            # 解析返回数据，提取指定平台数量
            data = result.get("data", [])
            if not isinstance(data, list):
                raise RuntimeError(f"返回数据格式错误: {type(data)}")
            
            # 查找指定平台的数量
            platform_count = 0
            total_all = 0
            for item in data:
                if isinstance(item, dict):
                    name = item.get("name", "")
                    value = item.get("value", 0)
                    try:
                        v_int = int(value) if value else 0
                    except Exception:
                        v_int = 0
                    total_all += v_int
                    if not platform_name or platform_name == "ALL" or name == platform_name:
                        platform_count = v_int
                        if platform_name and platform_name != "ALL":
                            break
            
            if not platform_name or platform_name == "ALL":
                return total_all
            return platform_count
            
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
    
    return 0


def _calculate_proportional_counts(
    keyword_counts: Dict[str, int],
    target_total: int = 2000,
    *,
    min_per_key: int = 1,
) -> Dict[str, int]:
    """按比例分配 target_total；关键词场景 min_per_key=1；平台场景可对 0 计数使用 min_per_key=0。"""
    total_count = sum(keyword_counts.values())

    if total_count <= 0:
        keys = list(keyword_counts.keys())
        if not keys or target_total <= 0:
            return {}
        base, rem = divmod(target_total, len(keys))
        return {k: base + (1 if i < rem else 0) for i, k in enumerate(keys)}

    if total_count <= target_total:
        return dict(keyword_counts)

    result: Dict[str, int] = {}
    proportions: Dict[str, float] = {}
    for keyword, count in keyword_counts.items():
        proportions[keyword] = (count / total_count) * target_total

    allocated_total = 0
    for keyword, proportion in proportions.items():
        allocated = int(proportion)
        if min_per_key > 0:
            result[keyword] = max(min_per_key, allocated)
        else:
            result[keyword] = max(0, allocated)
        allocated_total += result[keyword]

    remaining = target_total - allocated_total
    if remaining > 0:
        fractional_parts = [
            (keyword, proportion - int(proportion))
            for keyword, proportion in proportions.items()
        ]
        fractional_parts.sort(key=lambda x: x[1], reverse=True)
        for keyword, _ in fractional_parts:
            if remaining > 0:
                result[keyword] += 1
                remaining -= 1

    return result


def _allocate_across_platforms(platform_raw_counts: Dict[str, int], target_total: int) -> Dict[str, int]:
    """按各平台可见条数比例分配 target_total；计数为 0 的平台分得 0；全 0 时均分。"""
    if not platform_raw_counts:
        return {}
    positive = {k: v for k, v in platform_raw_counts.items() if v > 0}
    if not positive:
        return _calculate_proportional_counts(platform_raw_counts, target_total, min_per_key=0)
    allocated_pos = _calculate_proportional_counts(positive, target_total, min_per_key=0)
    out: Dict[str, int] = {k: 0 for k in platform_raw_counts}
    out.update(allocated_pos)
    return out


def _normalize_keyword_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    if m in ("advanced", "adv", "expr", "expression"):
        return "advanced"
    return "normal"


def _build_single_query_string(keywords: List[str], *, keyword_mode: str) -> str:
    """高级模式：单表达式；普通模式：多词用分号连接（或关系）。"""
    parts = [str(x).strip() for x in keywords if str(x).strip()]
    if not parts:
        return ""
    if _normalize_keyword_mode(keyword_mode) == "advanced":
        return parts[0]
    if len(parts) == 1:
        return parts[0]
    return ";".join(parts)


@tool
def data_num(
    searchWords: str,
    timeRange: str,
    threshold: int = 2000,
    platform: str = "微博",
    keywordMode: str = "normal",
    platforms: str = "",
    allocateByPlatform: bool = False,
) -> str:
    """
    描述：查询 NetInsight 检索式在指定条件下的条数，并生成 data_collect 可用的 search_matrix。
    - 普通模式（keywordMode=normal）：多个词将用分号合并为「或」检索（与 NetInsight 普通模式一致）。
    - 高级模式（keywordMode=advanced）：searchWords 应为单条表达式，支持 +（且）、|（或）、-（排除）与括号，例如：华为+(手机|(数据线+充电器))-小米
    - allocateByPlatform=true：对 platforms 列表中每个平台分别计数，再按条数比例把 threshold 分配到各平台（platform_allocation），避免总配额被单一渠道占满。
    输入补充：platforms 为 JSON 数组字符串，如 '["微博","微信"]'；allocateByPlatform 为 true 时忽略单平台 platform 字段，改为多平台分配。
    """
    import json as json_module

    def _truthy(v: object) -> bool:
        if isinstance(v, bool):
            return v
        s = str(v or "").strip().lower()
        return s in ("1", "true", "yes", "y", "on")

    # 解析搜索词列表
    try:
        if isinstance(searchWords, str):
            try:
                keywords = json_module.loads(searchWords)
                if not isinstance(keywords, list):
                    keywords = [str(keywords)]
            except json_module.JSONDecodeError:
                keywords = [searchWords]
        elif isinstance(searchWords, list):
            keywords = searchWords
        else:
            keywords = [str(searchWords)]
    except Exception:
        keywords = [str(searchWords)]

    if not keywords:
        return json_module.dumps(
            {
                "error": "搜索词列表不能为空",
                "search_matrix": {},
                "total_count": 0,
                "time_range": timeRange,
                "threshold": threshold,
            },
            ensure_ascii=False,
        )

    if threshold <= 0:
        return json_module.dumps(
            {
                "error": "数量阈值必须大于0",
                "search_matrix": {},
                "total_count": 0,
                "time_range": timeRange,
                "threshold": threshold,
            },
            ensure_ascii=False,
        )

    km = _normalize_keyword_mode(keywordMode)
    query_string = _build_single_query_string(keywords, keyword_mode=km)
    if not query_string.strip():
        return json_module.dumps(
            {
                "error": "检索式为空",
                "search_matrix": {},
                "total_count": 0,
                "time_range": timeRange,
                "threshold": threshold,
            },
            ensure_ascii=False,
        )

    allocate_by_platform = _truthy(allocateByPlatform) or _truthy(
        os.environ.get("SONA_DATA_NUM_ALLOCATE_BY_PLATFORM", "")
    )

    platform_list: List[str] = []
    if platforms and str(platforms).strip():
        try:
            loaded = json_module.loads(platforms)
            if isinstance(loaded, list):
                platform_list = [str(x).strip() for x in loaded if str(x).strip()]
        except json_module.JSONDecodeError:
            platform_list = []

    # 登录
    try:
        headers, cookies = _load_request_context()
    except ValueError as e:
        return json_module.dumps(
            {
                "error": str(e),
                "search_matrix": {},
                "total_count": 0,
                "time_range": timeRange,
                "threshold": threshold,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json_module.dumps(
            {
                "error": f"登录失败: {str(e)}",
                "search_matrix": {},
                "total_count": 0,
                "time_range": timeRange,
                "threshold": threshold,
            },
            ensure_ascii=False,
        )

    max_workers = max(1, min(int(os.getenv("SONA_DATA_NUM_MAX_WORKERS", "4")), 8))

    # ---------- 多平台：按平台分配 threshold ----------
    if allocate_by_platform and platform_list:
        normalized_list: List[str] = []
        for p in platform_list:
            np = _normalize_platform_name(p)
            if np and np != "ALL":
                normalized_list.append(np)
        if not normalized_list:
            return json_module.dumps(
                {
                    "error": "platforms 中无有效平台名称",
                    "search_matrix": {},
                    "total_count": 0,
                    "time_range": timeRange,
                    "threshold": threshold,
                },
                ensure_ascii=False,
            )

        platform_raw: Dict[str, int] = {}
        errors: List[str] = []

        def _query_plat(pl_name: str) -> tuple[str, int, Optional[str]]:
            try:
                c = _query_weibo_count(query_string, timeRange, headers, cookies, platform_name=pl_name)
                return pl_name, int(c), None
            except Exception as e:
                return pl_name, 0, str(e)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_query_plat, pl): pl for pl in normalized_list}
            for future in as_completed(futures):
                pl = futures[future]
                try:
                    name, cnt, err = future.result()
                except Exception as e:
                    name, cnt, err = pl, 0, str(e)
                platform_raw[name] = cnt
                if err:
                    errors.append(f"{name}: {err}")

        if errors and all(v == 0 for v in platform_raw.values()):
            return json_module.dumps(
                {
                    "error": "; ".join(errors),
                    "search_matrix": {},
                    "total_count": 0,
                    "time_range": timeRange,
                    "threshold": threshold,
                },
                ensure_ascii=False,
            )

        platform_allocation = _allocate_across_platforms(platform_raw, threshold)
        # data_collect 用的矩阵：单键为完整检索式，值为「总配额」（各平台分别采集时用 platform_allocation）
        final_counts = {query_string: threshold}
        total_alloc = sum(platform_allocation.values())

        result: Dict[str, object] = {
            "search_matrix": final_counts,
            "total_count": total_alloc,
            "platform": normalized_list[0] if len(normalized_list) == 1 else "MULTI",
            "time_range": timeRange,
            "threshold": threshold,
            "keyword_mode": km,
            "query_string": query_string,
            "allocate_by_platform": True,
            "platform_counts": platform_raw,
            "platform_allocation": platform_allocation,
        }
        if errors:
            result["warnings"] = errors
        return json_module.dumps(result, ensure_ascii=False)

    # ---------- 单平台：单条检索式计数 + 按比例压到 threshold ----------
    normalized_platform = _normalize_platform_name(platform or "微博")
    if not normalized_platform:
        return json_module.dumps(
            {
                "error": (
                    f"不支持的平台: {platform}。"
                    f"当前支持: {', '.join(SUPPORTED_PLATFORMS)}，以及 ALL"
                ),
                "search_matrix": {},
                "total_count": 0,
                "time_range": timeRange,
                "threshold": threshold,
            },
            ensure_ascii=False,
        )

    errors = []

    try:
        cnt = _query_weibo_count(
            query_string,
            timeRange,
            headers,
            cookies,
            platform_name=normalized_platform,
        )
        raw_cnt = int(cnt)
    except Exception as e:
        errors.append(str(e))
        raw_cnt = 0

    if raw_cnt <= 0 and errors:
        return json_module.dumps(
            {
                "error": "; ".join(errors),
                "search_matrix": {},
                "total_count": 0,
                "time_range": timeRange,
                "threshold": threshold,
            },
            ensure_ascii=False,
        )

    if raw_cnt <= 0:
        final_counts = {query_string: 0}
    else:
        final_counts = _calculate_proportional_counts(
            {query_string: raw_cnt},
            target_total=threshold,
            min_per_key=0,
        )

    total_count = sum(final_counts.values())
    result = {
        "search_matrix": final_counts,
        "total_count": total_count,
        "platform": normalized_platform,
        "time_range": timeRange,
        "threshold": threshold,
        "keyword_mode": km,
        "query_string": query_string,
        "allocate_by_platform": False,
    }
    if errors:
        result["warnings"] = errors
    return json_module.dumps(result, ensure_ascii=False)
