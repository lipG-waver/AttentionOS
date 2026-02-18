"""
测试 ModelScope API Token 有效性
"""

import os
import pytest
from dotenv import load_dotenv
from openai import OpenAI, AuthenticationError, APIConnectionError


# 加载 .env 文件中的环境变量
load_dotenv()


class TestModelScopeAPI:
    """ModelScope API 测试类"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """测试前置设置"""
        self.api_key = os.getenv("MODELSCOPE_ACCESS_TOKEN")
        self.base_url = "https://api-inference.modelscope.cn/v1/"
        self.model = "Qwen/Qwen2.5-Coder-32B-Instruct"

    def test_api_key_exists(self):
        """测试 API Key 是否存在"""
        assert self.api_key is not None, "MODELSCOPE_ACCESS_TOKEN 未在 .env 中设置"
        assert self.api_key != "", "MODELSCOPE_ACCESS_TOKEN 不能为空"

    def test_api_key_format(self):
        """测试 API Key 格式是否有效"""
        assert self.api_key is not None, "MODELSCOPE_ACCESS_TOKEN 未设置"
        # ModelScope token 通常有一定长度
        assert len(self.api_key) > 10, "MODELSCOPE_ACCESS_TOKEN 长度异常"

    def test_api_connection(self):
        """测试 API 连接是否正常"""
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": "hello"}
                ],
                max_tokens=10  # 限制 token 数量以快速完成测试
            )
            assert response is not None
            assert response.choices is not None
            assert len(response.choices) > 0
        except AuthenticationError:
            pytest.fail("API Token 无效，认证失败")
        except APIConnectionError:
            pytest.fail("API 连接失败，请检查网络或 base_url")

    def test_api_stream_response(self):
        """测试流式响应是否正常"""
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "说一个字"}
                ],
                stream=True,
                max_tokens=10
            )

            chunks_received = 0
            for chunk in response:
                chunks_received += 1
                if chunks_received > 0:
                    break  # 收到数据即可，不需要完整响应

            assert chunks_received > 0, "未收到流式响应数据"

        except AuthenticationError:
            pytest.fail("API Token 无效，认证失败")
        except APIConnectionError:
            pytest.fail("API 连接失败，请检查网络或 base_url")


def test_token_validity_quick():
    """快速验证 Token 有效性（独立函数形式）"""
    load_dotenv()
    api_key = os.getenv("MODELSCOPE_ACCESS_TOKEN")

    assert api_key, "请在 .env 文件中设置 MODELSCOPE_ACCESS_TOKEN"

    client = OpenAI(
        api_key=api_key,
        base_url="https://api-inference.modelscope.cn/v1/"
    )

    response = client.chat.completions.create(
        model="Qwen/Qwen2.5-Coder-32B-Instruct",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=5
    )

    assert response.choices[0].message.content, "API 响应为空"


if __name__ == "__main__":
    # 直接运行时执行快速测试
    pytest.main([__file__, "-v"])