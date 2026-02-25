"""
软件与网页数据库生成模块
从work_logs.json中提取历史数据，使用大语言模型推理生成用户电脑上安装的软件和常用网页列表
"""
import json
import logging
import re
from pathlib import Path
from openai import OpenAI

from attention.config import Config
from attention.core.llm_provider import get_llm_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 模型名（端点和 key 在调用时从 llm_provider 动态获取，无需环境变量）
DEEPSEEK_MODEL = Config.TEXT_MODEL_NAME


def _get_active_provider_cfg():
    """获取当前激活提供商的配置"""
    client = get_llm_provider()
    return client.get_config(client.get_active_provider())

# 软件数据库文件路径
APP_DATABASE_FILE = Config.DATA_DIR / "installed_apps.json"


def load_work_logs() -> list:
    """加载工作日志"""
    if not Config.DATABASE_FILE.exists():
        logger.warning("work_logs.json 不存在")
        return []
    
    with open(Config.DATABASE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_apps_and_websites_from_logs(logs: list) -> tuple[set, set]:
    """从日志中提取所有检测到的应用程序和网页"""
    apps = set()
    websites = set()
    
    for record in logs:
        analysis = record.get("analysis", {})
        
        # 提取活动窗口应用
        for app in analysis.get("applications_detected", []):
            if app:
                apps.add(app)
        
        # 提取任务栏应用
        for app in analysis.get("taskbar_apps", []):
            if app:
                apps.add(app)
        
        # 从details中提取网页信息
        details = analysis.get("details", "")
        if details:
            # 提取可能的网站名称和URL
            extracted = extract_websites_from_text(details)
            websites.update(extracted)
        
        # 从content_type中提取
        content_type = analysis.get("content_type", "")
        if content_type:
            websites.add(content_type)
    
    return apps, websites


def extract_websites_from_text(text: str) -> set:
    """从文本中提取网站相关信息"""
    websites = set()
    
    # 常见网站关键词
    website_keywords = [
        "GitHub", "Bilibili", "B站", "YouTube", "Google", "百度", "知乎", "微博",
        "CSDN", "掘金", "StackOverflow", "LeetCode", "力扣", "豆瓣", "淘宝", "京东",
        "网易", "腾讯", "阿里", "抖音", "小红书", "Twitter", "X", "Reddit", "Discord",
        "Notion", "Figma", "Slack", "Trello", "GitLab", "Jira", "Confluence"
    ]
    
    for keyword in website_keywords:
        if keyword.lower() in text.lower():
            websites.add(keyword)
    
    # 提取URL模式
    url_pattern = r'(?:https?://)?(?:www\.)?([a-zA-Z0-9-]+(?:\.[a-zA-Z]{2,})+)'
    matches = re.findall(url_pattern, text)
    for match in matches:
        websites.add(match)
    
    return websites


def generate_database(raw_apps: set, raw_websites: set) -> dict:
    """使用大语言模型推理生成规范化的软件和网页数据库"""
    if not raw_apps and not raw_websites:
        logger.warning("没有检测到任何应用程序或网页")
        return {"apps": [], "websites": []}
    
    apps_list = list(raw_apps)
    websites_list = list(raw_websites)
    
    prompt = f"""以下是从用户电脑屏幕截图中识别出的应用程序和网页信息（可能有重复、别名、识别错误等）：

**应用程序列表：**
{json.dumps(apps_list, ensure_ascii=False, indent=2)}

**网页/网站列表：**
{json.dumps(websites_list, ensure_ascii=False, indent=2)}

请分析这些数据，推理出用户电脑上实际安装的软件和常用网页列表。要求：
1. 合并同一软件/网站的不同名称（如"VSCode"和"Visual Studio Code"是同一软件，"B站"和"Bilibili"是同一网站）
2. 修正明显的识别错误
3. 对软件和网页进行分类（工作、沟通、娱乐、学习等）
4. 只保留真实存在的软件和网站，去除无意义的识别结果

请按以下JSON格式输出（只输出JSON，不要其他内容）：
{{
  "apps": [
    {{
      "name": "软件标准名称",
      "aliases": ["可能的别名1", "可能的别名2"],
      "category": "工作|沟通|娱乐|学习|系统工具|浏览器|其他"
    }}
  ],
  "websites": [
    {{
      "name": "网站标准名称",
      "url": "网站主域名（如 github.com）",
      "aliases": ["可能的别名1", "可能的别名2"],
      "category": "工作|沟通|娱乐|学习|购物|社交|其他"
    }}
  ]
}}"""

    _cfg = _get_active_provider_cfg()
    client = OpenAI(
        base_url=_cfg.api_base if _cfg else Config.QWEN_API_BASE,
        api_key=_cfg.api_key if _cfg else ""
    )
    
    logger.info("正在调用大语言模型进行软件和网页数据库推理...")
    
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
        extra_body={"enable_thinking": True}
    )
    
    # 收集响应
    full_response = ""
    done_thinking = False
    
    for chunk in response:
        if chunk.choices:
            thinking_chunk = chunk.choices[0].delta.reasoning_content
            answer_chunk = chunk.choices[0].delta.content
            
            if thinking_chunk:
                print(thinking_chunk, end='', flush=True)
            elif answer_chunk:
                if not done_thinking:
                    print('\n\n=== 推理结果 ===\n')
                    done_thinking = True
                print(answer_chunk, end='', flush=True)
                full_response += answer_chunk
    
    print("\n")
    
    # 解析JSON
    try:
        json_str = full_response.strip()
        if "```json" in json_str:
            start = json_str.find("```json") + 7
            end = json_str.find("```", start)
            json_str = json_str[start:end].strip()
        elif "```" in json_str:
            start = json_str.find("```") + 3
            end = json_str.find("```", start)
            json_str = json_str[start:end].strip()
        else:
            start = json_str.find("{")
            end = json_str.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = json_str[start:end]
        
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"解析响应失败: {e}")
        return {"apps": [], "websites": [], "error": str(e)}


def save_database(database: dict):
    """保存软件和网页数据库"""
    Config.ensure_dirs()
    
    with open(APP_DATABASE_FILE, "w", encoding="utf-8") as f:
        json.dump(database, f, ensure_ascii=False, indent=2)
    
    logger.info(f"软件和网页数据库已保存到: {APP_DATABASE_FILE}")


def load_database() -> dict:
    """加载软件和网页数据库"""
    if not APP_DATABASE_FILE.exists():
        return {"apps": [], "websites": []}
    
    with open(APP_DATABASE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def find_app_by_name(name: str, database: dict = None) -> dict:
    """根据名称在数据库中查找软件"""
    if database is None:
        database = load_database()
    
    name_lower = name.lower()
    
    for app in database.get("apps", []):
        # 匹配标准名称
        if app.get("name", "").lower() == name_lower:
            return app
        
        # 匹配别名
        for alias in app.get("aliases", []):
            if alias.lower() == name_lower:
                return app
    
    return None


def find_website_by_name(name: str, database: dict = None) -> dict:
    """根据名称在数据库中查找网站"""
    if database is None:
        database = load_database()
    
    name_lower = name.lower()
    
    for website in database.get("websites", []):
        # 匹配标准名称
        if website.get("name", "").lower() == name_lower:
            return website
        
        # 匹配URL
        if website.get("url", "").lower() == name_lower:
            return website
        
        # 匹配别名
        for alias in website.get("aliases", []):
            if alias.lower() == name_lower:
                return website
    
    return None


def find_by_name(name: str, database: dict = None) -> dict:
    """根据名称在数据库中查找软件或网站"""
    if database is None:
        database = load_database()
    
    # 先查软件
    result = find_app_by_name(name, database)
    if result:
        result["type"] = "app"
        return result
    
    # 再查网站
    result = find_website_by_name(name, database)
    if result:
        result["type"] = "website"
        return result
    
    return None


def main():
    """主函数"""
    print("=" * 50)
    print("软件与网页数据库生成工具")
    print("=" * 50)
    
    # 1. 加载工作日志
    logs = load_work_logs()
    print(f"加载了 {len(logs)} 条工作日志记录")
    
    if not logs:
        print("没有工作日志数据，请先运行 main.py 收集一些数据")
        return
    
    # 2. 提取应用程序和网页
    raw_apps, raw_websites = extract_apps_and_websites_from_logs(logs)
    print(f"从日志中提取了 {len(raw_apps)} 个不同的应用程序名称")
    print(f"从日志中提取了 {len(raw_websites)} 个不同的网页/网站")
    print(f"原始应用列表: {', '.join(raw_apps)}")
    print(f"原始网页列表: {', '.join(raw_websites)}")
    
    # 3. 使用LLM推理生成数据库
    database = generate_database(raw_apps, raw_websites)
    
    # 4. 保存数据库
    save_database(database)
    
    # 5. 显示结果
    print("\n" + "=" * 50)
    print("数据库生成完成")
    print("=" * 50)
    
    print("\n【已安装软件】")
    for app in database.get("apps", []):
        print(f"  [{app.get('category', '未知')}] {app.get('name')}")
        if app.get("aliases"):
            print(f"    别名: {', '.join(app.get('aliases', []))}")
    
    print("\n【常用网页】")
    for website in database.get("websites", []):
        print(f"  [{website.get('category', '未知')}] {website.get('name')} ({website.get('url', '')})")
        if website.get("aliases"):
            print(f"    别名: {', '.join(website.get('aliases', []))}")


if __name__ == "__main__":
    main()
