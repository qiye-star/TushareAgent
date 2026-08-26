"""多模态图块描述：Captioner 协议 + Noop（默认）/ VisionCaptioner（deepseek-v4-flash-vision-exp）。

PDF 中 kind==image 且无内嵌文本的图/表块，用视觉模型描述成文本入库。未配置/失败 → Noop 跳过，
绝不编造描述。真实视觉端点经现有 DeepSeek/OpenAI 兼容 client 调用。
"""

from __future__ import annotations

import base64
from typing import Protocol

from demomcp.config.settings import Settings

_PROMPT = (
    "这是一张来自中国上市公司年报的图表/图片。请用简洁的中文描述它的内容："
    "标题、坐标/指标含义、关键数值与趋势。若无法辨认，请只说无法辨认，不要编造。"
)


class Captioner(Protocol):
    def caption(self, image: bytes, *, context: str, page: int) -> str | None: ...


class NoopCaptioner:
    """默认：无视觉模型 → 返回 None，图块跳过。"""

    def caption(self, image: bytes, *, context: str, page: int) -> str | None:
        return None


class VisionCaptioner:
    """真实多模态模型：复用 DeepSeek/OpenAI 兼容 client 发 base64 图 → 描述文本。"""

    def __init__(self, model_name: str = "deepseek-v4-flash-vision-exp", *, api_key: str, base_url: str) -> None:
        from openai import OpenAI  # 惰性import；openai 为既有依赖

        self.model_name = model_name
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def caption(self, image: bytes, *, context: str, page: int) -> str | None:
        try:
            data_uri = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
            msg = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"{_PROMPT}\n所在章节：{context}；页码：第{page}页。"},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
                max_tokens=256,
            )
            content = msg.choices[0].message.content
            return (content or "").strip() or None
        except Exception:  # noqa: BLE001 - 单块失败不崩摄取
            return None


def build_captioner(config: Settings) -> Captioner:
    """rag_captioner 非空且 ds_api_key 存在 → VisionCaptioner；否则 Noop。"""
    if config.rag_captioner and config.ds_api_key:
        try:
            return VisionCaptioner(
                config.rag_captioner, api_key=config.ds_api_key, base_url=config.ds_base_url
            )
        except Exception:  # noqa: BLE001 - 建不出就回退 Noop
            return NoopCaptioner()
    return NoopCaptioner()
