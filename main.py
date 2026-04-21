from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.message.components import Image, Reply
import subprocess
import sys
import aiohttp
import tempfile
import os
import asyncio
from PIL import Image as PILImage

@register("对称", "YourName", "一个简单的对称插件", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        try:
            from PIL import Image as PILImage
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
            print("Pillow安装完成")
            from PIL import Image as PILImage

    def _save_mirror_image(self, img_bytes: bytes, mode: str, save_path: str):
        from io import BytesIO
        from PIL import Image as PILImage, ImageSequence
        
        img = PILImage.open(BytesIO(img_bytes))
        
        # 检查是否是动图 (GIF)
        if getattr(img, "is_animated", False):
            frames = []
            # 遍历 GIF 的每一帧
            for frame in ImageSequence.Iterator(img):
                # 转换模式并处理镜像
                f = frame.convert("RGBA")
                processed_frame = self._apply_mirror_to_pil(f, mode)
                
                # 尽量保留原帧的持续时间 (duration)
                duration = frame.info.get('duration', 100)
                processed_frame.info['duration'] = duration
                frames.append(processed_frame)
            
            # 重新封装成 GIF 保存
            frames[0].save(
                save_path, 
                save_all=True, 
                append_images=frames[1:], 
                loop=img.info.get('loop', 0),
                duration=[f.info.get('duration', 100) for f in frames],
                format="GIF",
                disposal=2 # 关键：防止多帧重叠
            )
        else:
            # 静态图处理
            img = img.convert("RGBA")
            new_img = self._apply_mirror_to_pil(img, mode)
            new_img.save(save_path, "PNG")

    def _apply_mirror_to_pil(self, img, mode):
        """核心镜像算法，输入 PIL 对象，输出镜像后的 PIL 对象"""
        from PIL import Image as PILImage
        w, h = img.size
        new_img = PILImage.new("RGBA", (w, h))
        
        if mode == "右":
            part = img.crop((w // 2, 0, w, h))
            new_img.paste(part.transpose(PILImage.FLIP_LEFT_RIGHT), (0, 0))
            new_img.paste(part, (w // 2, 0))
        elif mode == "上":
            part = img.crop((0, 0, w, h // 2))
            new_img.paste(part, (0, 0))
            new_img.paste(part.transpose(PILImage.FLIP_TOP_BOTTOM), (0, h // 2))
        elif mode == "下":
            part = img.crop((0, h // 2, w, h))
            new_img.paste(part.transpose(PILImage.FLIP_TOP_BOTTOM), (0, 0))
            new_img.paste(part, (0, h // 2))
        else: # 默认左
            part = img.crop((0, 0, w // 2, h))
            new_img.paste(part, (0, 0))
            new_img.paste(part.transpose(PILImage.FLIP_LEFT_RIGHT), (w // 2, 0))
            
        return new_img

    @filter.command("对称")
    async def mirror(self, event: AstrMessageEvent):
        # 解析命令参数
        message_str = event.message_str.strip()
        # 使用空格分割，移除命令本身，获取剩余参数
        parts = message_str.split(maxsplit=1)
        mode = parts[1].strip().lower() if len(parts) > 1 else "左"

        messages = event.get_messages()
        reply_msg = None
        for comp in messages:
            if isinstance(comp, Reply):
                reply_msg = comp
                break

        if not reply_msg:
            yield event.plain_result("请回复一张图片喵！")
            return

        image_url = await self._get_image_from_reply(event, reply_msg)
        if not image_url:
            yield event.plain_result("请回复一张图片喵！")
            return

        logger.info(f"获取到图片URL: {image_url}, mode: {mode}")

        # 下载图片
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    if resp.status != 200:
                        yield event.plain_result("图片打不开喵...")
                        return
                    img_data = await resp.read()

            tmp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            
            # 执行镜像逻辑
            self._save_mirror_image(img_data, mode, tmp_file.name)
            yield event.chain_result([Image.fromFileSystem(tmp_file.name)])

        except Exception as e:
            yield event.plain_result("处理图片失败喵...")
            logger.error(f"处理图片失败: {e}")

        finally:
            if tmp_file.name and os.path.exists(tmp_file.name):
                # 异步延迟删除，防止文件还在传输时被删
                async def delay_delete(path):
                    await asyncio.sleep(10)
                    try: os.unlink(path)
                    except: pass
                asyncio.create_task(delay_delete(tmp_file.name))

    
    # 抄来的 来自https://github.com/FlanChanXwO/astrbot_plugin_imgexploration
    @staticmethod
    async def _get_image_from_reply(
        event: AstrMessageEvent, reply: Reply
    ) -> str | None:
        """从回复消息中提取图片 URL.

        Args:
            event: 消息事件
            reply: 回复组件

        Returns:
            图片 URL，失败返回 None
        """
        # 尝试通过 bot API 获取原消息
        bot = getattr(event, "bot", None)
        if bot:
            try:
                # 获取原消息内容
                msg_resp = await bot.call_action("get_msg", message_id=int(reply.id))
                if msg_resp and "message" in msg_resp:
                    # 解析消息中的图片
                    for seg in msg_resp["message"]:
                        if seg.get("type") == "image":
                            data = seg.get("data", {})
                            # 优先使用 url 字段
                            url = data.get("url")
                            if url:
                                return url
            except Exception as e:
                logger.debug(f"[ImgExploration] 获取回复消息失败: {e}")

        # 回退：检查当前消息链中是否有图片（直接回复图片的情况）
        messages = event.get_messages()
        for comp in messages:
            if isinstance(comp, Image):
                if comp.url:
                    return comp.url
                if comp.file:
                    # 可能是本地文件或 base64
                    if comp.file.startswith(("http://", "https://")):
                        return comp.file

        return None
