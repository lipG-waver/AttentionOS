"""
语音识别模块 — 基于 ModelScope SenseVoice

使用阿里 FunASR 团队开源的 SenseVoiceSmall 模型，替代浏览器 Web Speech API。
支持中英日韩 50+ 语言，比 Whisper 快 15 倍，且附带语音情感识别和音频事件检测。

模型: iic/SenseVoiceSmall (ModelScope)
特性:
  - 多语言语音识别（自动语种检测）
  - 语音情感识别（happy / sad / angry / neutral / ...）
  - 音频事件检测（笑声、掌声、背景噪音等）
  - 逆文本标准化（ITN）：自动将口语数字转为阿拉伯数字

使用方式:
  from attention.core.speech_recognition import get_speech_recognizer
  result = get_speech_recognizer().transcribe("/path/to/audio.wav")
  # result = {"text": "明天下午完成报告", "emotion": "neutral", "language": "zh"}
"""
import logging
import tempfile
import os
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# SenseVoice 模型配置
SENSEVOICE_MODEL = "iic/SenseVoiceSmall"


class SpeechRecognizer:
    """
    基于 SenseVoice 的语音识别器。

    支持两种模式：
    1. 本地推理（需安装 funasr）：CPU 即可运行，延迟低
    2. 降级模式：返回错误提示，引导用户安装依赖
    """

    def __init__(self):
        self._model = None
        self._available = False
        self._init_model()

    def _init_model(self):
        """尝试加载 SenseVoice 模型"""
        try:
            from funasr import AutoModel

            self._model = AutoModel(
                model=SENSEVOICE_MODEL,
                trust_remote_code=True,
                vad_model="fsmn-vad",
                vad_kwargs={"max_single_segment_time": 30000},
                device="cpu",  # 小模型 CPU 也很快
            )
            self._available = True
            logger.info("SenseVoice 模型加载成功")
        except ImportError:
            logger.warning(
                "funasr 未安装，语音识别不可用。"
                "请运行: pip install funasr modelscope torch torchaudio"
            )
        except Exception as e:
            logger.error(f"SenseVoice 模型加载失败: {e}")

    @property
    def is_available(self) -> bool:
        return self._available

    def transcribe(self, audio_path: str, language: str = "auto") -> Dict[str, Any]:
        """
        转录音频文件。

        Args:
            audio_path: 音频文件路径（支持 wav, mp3, flac 等）
            language:   语言代码，"auto" 自动检测

        Returns:
            {
                "text": "识别文本",
                "emotion": "neutral",   # 情感标签
                "language": "zh",       # 检测到的语言
                "success": True
            }
        """
        if not self._available:
            return {
                "text": "",
                "emotion": None,
                "language": None,
                "success": False,
                "error": "SenseVoice 模型未加载，请安装 funasr",
            }

        if not os.path.exists(audio_path):
            return {
                "text": "",
                "success": False,
                "error": f"音频文件不存在: {audio_path}",
            }

        try:
            res = self._model.generate(
                input=audio_path,
                cache={},
                language=language,
                use_itn=True,
                batch_size_s=60,
            )

            if not res or len(res) == 0:
                return {"text": "", "success": False, "error": "识别结果为空"}

            result = res[0]
            text = result.get("text", "")

            # SenseVoice 会在文本中嵌入特殊标签，如 <|HAPPY|>, <|zh|> 等
            emotion = self._extract_emotion(text)
            detected_lang = self._extract_language(text)
            clean_text = self._clean_text(text)

            return {
                "text": clean_text,
                "emotion": emotion,
                "language": detected_lang,
                "success": True,
            }
        except Exception as e:
            logger.error(f"语音识别失败: {e}")
            return {"text": "", "success": False, "error": str(e)}

    def transcribe_bytes(self, audio_bytes: bytes, suffix: str = ".wav") -> Dict[str, Any]:
        """
        转录音频字节数据（供 API 端点使用）。

        Args:
            audio_bytes: 音频二进制数据
            suffix:      文件后缀名

        Returns:
            同 transcribe()
        """
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            return self.transcribe(tmp_path)
        finally:
            os.unlink(tmp_path)

    @staticmethod
    def _extract_emotion(text: str) -> Optional[str]:
        """从 SenseVoice 输出中提取情感标签"""
        emotion_map = {
            "<|HAPPY|>": "happy",
            "<|SAD|>": "sad",
            "<|ANGRY|>": "angry",
            "<|NEUTRAL|>": "neutral",
            "<|FEARFUL|>": "fearful",
            "<|DISGUSTED|>": "disgusted",
            "<|SURPRISED|>": "surprised",
        }
        for tag, label in emotion_map.items():
            if tag in text:
                return label
        return "neutral"

    @staticmethod
    def _extract_language(text: str) -> Optional[str]:
        """从 SenseVoice 输出中提取语言标签"""
        lang_map = {
            "<|zh|>": "zh",
            "<|en|>": "en",
            "<|ja|>": "ja",
            "<|ko|>": "ko",
            "<|yue|>": "yue",
        }
        for tag, lang in lang_map.items():
            if tag in text:
                return lang
        return None

    @staticmethod
    def _clean_text(text: str) -> str:
        """清理 SenseVoice 输出中的特殊标签"""
        import re
        # 移除所有 <|...|> 标签
        cleaned = re.sub(r"<\|[^|]+\|>", "", text)
        return cleaned.strip()


# ================================================================== #
#  单例
# ================================================================== #

_recognizer: Optional[SpeechRecognizer] = None


def get_speech_recognizer() -> SpeechRecognizer:
    """获取 SpeechRecognizer 单例"""
    global _recognizer
    if _recognizer is None:
        _recognizer = SpeechRecognizer()
    return _recognizer
