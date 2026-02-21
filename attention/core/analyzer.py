"""
多模态分析模块
使用Qwen-VL模型分析屏幕截图，推断用户工作状态
"""
import base64
import json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

from openai import OpenAI

from attention.config import Config

logger = logging.getLogger(__name__)


# 分析提示词
ANALYSIS_PROMPT = """你是一个工作状态分析专家。请分析以下电脑屏幕截图，推断用户当前的工作状态。

**重要：只输出你在截图中实际看到的内容，不要编造或猜测任何不存在的应用程序！**

屏幕内容分析要求：
1. 识别屏幕上所有可见的应用和窗口
2. **重点关注屏幕底部的任务栏**：仔细识别任务栏中每一个程序图标，只列出你确实能看到的图标
3. 判断主要活动内容（编程、写作、浏览、沟通等）
4. 评估用户意图（工作、学习、娱乐、混合）

请按以下JSON格式输出分析结果（只输出JSON，不要其他内容）：
{
  "work_status": "高效工作|沟通协调|学习研究|休闲娱乐|混合状态",
  "details": "详细的文字描述",
  "applications_detected": ["应用1", "应用2"],
  "taskbar_apps": ["任务栏程序1", "任务栏程序2"],
  "content_type": "具体的活动类型"
}

注意：
- applications_detected：当前屏幕上可见的活动窗口
- taskbar_apps：任务栏中你实际能看到的程序图标（不要猜测，看不清就不要写）

判断标准：
- 高效工作：编程IDE（VSCode、PyCharm等）、办公软件（Word、Excel等）、专业工具
- 沟通协调：微信工作群、钉钉、飞书、邮件客户端、视频会议等
- 学习研究：教育网站、技术文档、在线课程、学术论文等
- 休闲娱乐：视频网站（B站、YouTube等）娱乐内容、社交媒体、游戏等
- 混合状态：同时进行多项活动（结合任务栏中的后台程序综合判断）"""


@dataclass
class AnalysisResult:
    """分析结果数据类"""
    work_status: str = "未知"
    details: str = ""
    applications_detected: list = None
    taskbar_apps: list = None
    content_type: str = "未知"
    
    def __post_init__(self):
        if self.applications_detected is None:
            self.applications_detected = []
        if self.taskbar_apps is None:
            self.taskbar_apps = []
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ScreenAnalyzer:
    """屏幕分析器"""

    def __init__(self):
        self.config = Config
        self._client = OpenAI(
            base_url=self.config.QWEN_API_BASE,
            api_key=self.config.QWEN_API_KEY,
        )
    
    def analyze(self, image_data: bytes) -> tuple[AnalysisResult, str]:
        """
        分析屏幕截图
        
        Args:
            image_data: 图像的字节数据
            
        Returns:
            Tuple[分析结果, 原始响应]
        """
        if not image_data:
            logger.error("图像数据为空")
            return AnalysisResult(details="图像数据为空"), ""
        
        # 重试机制
        for attempt in range(self.config.MAX_RETRIES):
            try:
                raw_response = self._call_api(image_data)
                result = self._parse_response(raw_response)
                return result, raw_response
            except Exception as e:
                logger.warning(f"分析失败 (尝试 {attempt + 1}/{self.config.MAX_RETRIES}): {e}")
                if attempt < self.config.MAX_RETRIES - 1:
                    time.sleep(self.config.RETRY_DELAY)
        
        return AnalysisResult(details="分析失败，已达最大重试次数"), ""
    
    def _call_api(self, image_data: bytes) -> str:
        """调用 Qwen-VL API（使用 openai 客户端）"""
        image_base64 = base64.b64encode(image_data).decode("utf-8")

        response = self._client.chat.completions.create(
            model=self.config.MODEL_NAME,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                    },
                    {
                        "type": "text",
                        "text": ANALYSIS_PROMPT,
                    },
                ],
            }],
            max_tokens=1000,
            temperature=0.3,
        )
        return response.choices[0].message.content
    
    def _parse_response(self, response: str) -> AnalysisResult:
        """解析模型响应"""
        if not response:
            return AnalysisResult(details="模型响应为空")
        
        try:
            # 尝试提取JSON
            json_str = self._extract_json(response)
            data = json.loads(json_str)
            
            return AnalysisResult(
                work_status=data.get("work_status", "未知"),
                details=data.get("details", ""),
                applications_detected=data.get("applications_detected", []),
                taskbar_apps=data.get("taskbar_apps", []),
                content_type=data.get("content_type", "未知")
            )
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"解析响应失败: {e}")
            # 返回包含原始响应的结果
            return AnalysisResult(details=f"解析失败，原始响应: {response[:500]}")
    
    def _extract_json(self, text: str) -> str:
        """从文本中提取JSON字符串"""
        # 尝试找到JSON块
        text = text.strip()
        
        # 如果包含markdown代码块
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()
        
        if "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()
        
        # 尝试找到第一个{和最后一个}
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return text[start:end]
        
        return text


# 模块级便捷函数
_analyzer = None

def get_analyzer() -> ScreenAnalyzer:
    """获取分析器单例"""
    global _analyzer
    if _analyzer is None:
        _analyzer = ScreenAnalyzer()
    return _analyzer

def analyze_screen(image_data: bytes) -> tuple[AnalysisResult, str]:
    """分析屏幕的便捷函数"""
    return get_analyzer().analyze(image_data)
