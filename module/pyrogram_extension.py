"""Pyrogram ext"""

import asyncio
import os
import re
import subprocess
import secrets
import struct
import time
from datetime import datetime
from functools import wraps
from io import BytesIO, StringIO
from mimetypes import MimeTypes
from typing import Callable, Iterable, List, Optional, Union

import pyrogram
from loguru import logger
from pyrogram import types
from pyrogram.client import Cache
from pyrogram.file_id import (
    FILE_REFERENCE_FLAG,
    PHOTO_TYPES,
    WEB_LOCATION_FLAG,
    FileType,
    b64_decode,
    rle_decode,
)
from pyrogram.mime_types import mime_types

from module.app import (
    Application,
    CloudDriveUploadStat,
    DownloadStatus,
    ForwardStatus,
    TaskNode,
    UploadProgressStat,
    UploadStatus,
)
from module.download_stat import get_download_result
from module.language import Language, _t
from module.send_media_group_v2 import cache_media, send_media_group_v2
from utils.format import (
    create_progress_bar,
    extract_info_from_link,
    format_byte,
    truncate_filename,
)
from utils.meta_data import MetaData

_mimetypes = MimeTypes()
_mimetypes.readfp(StringIO(mime_types))
_download_cache = Cache(1024 * 1024 * 1024)


def reset_download_cache():
    """Reset download cache"""
    _download_cache.store.clear()


def _guess_mime_type(filename: str) -> Optional[str]:
    """Guess mime type"""
    return _mimetypes.guess_type(filename)[0]


def _guess_extension(mime_type: str) -> Optional[str]:
    """Guess extension"""
    return _mimetypes.guess_extension(mime_type)


def get_media_obj(
    message: pyrogram.types.Message, media: str = None, caption: str = None
) -> Union[
    types.InputMediaPhoto,
    types.InputMediaVideo,
    types.InputMediaAudio,
    types.InputMediaDocument,
    types.InputMediaAnimation,
]:
    """Get media object"""
    media_type = message.media
    if media_type == pyrogram.enums.MessageMediaType.PHOTO:
        return types.InputMediaPhoto(media, caption=caption)

    if media_type == pyrogram.enums.MessageMediaType.VIDEO:
        return types.InputMediaVideo(
            media,
            caption=caption,
            width=message.video.width,
            height=message.video.height,
            duration=message.video.duration,
        )

    if media_type in [
        pyrogram.enums.MessageMediaType.AUDIO,
        pyrogram.enums.MessageMediaType.VOICE,
    ]:
        return types.InputMediaAudio(media, caption=caption)

    if media_type == pyrogram.enums.MessageMediaType.DOCUMENT:
        return types.InputMediaDocument(media, caption=caption)

    if media_type == pyrogram.enums.MessageMediaType.ANIMATION:
        return types.InputMediaAnimation(media, caption=caption)

    return None


def _get_file_type(file_id: str):
    """Get file type"""
    decoded = rle_decode(b64_decode(file_id))

    # File id versioning. Major versions lower than 4 don't have a minor version
    major = decoded[-1]

    if major < 4:
        buffer = BytesIO(decoded[:-1])
    else:
        buffer = BytesIO(decoded[:-2])

    file_type, _ = struct.unpack("<ii", buffer.read(8))

    file_type &= ~WEB_LOCATION_FLAG
    file_type &= ~FILE_REFERENCE_FLAG

    try:
        file_type = FileType(file_type)
    except ValueError as exc:
        raise ValueError(f"Unknown file_type {file_type} of file_id {file_id}") from exc

    return file_type


def get_extension(file_id: str, mime_type: str, dot: bool = True) -> str:
    """Get extension"""

    if not file_id:
        if dot:
            return ".unknown"
        return "unknown"

    file_type = _get_file_type(file_id)

    guessed_extension = _guess_extension(mime_type)

    if file_type in PHOTO_TYPES:
        extension = "jpg"
    elif file_type == FileType.VOICE:
        extension = guessed_extension or "ogg"
    elif file_type in (FileType.VIDEO, FileType.ANIMATION, FileType.VIDEO_NOTE):
        extension = guessed_extension or "mp4"
    elif file_type == FileType.DOCUMENT:
        extension = guessed_extension or "zip"
    elif file_type == FileType.STICKER:
        extension = guessed_extension or "webp"
    elif file_type == FileType.AUDIO:
        extension = guessed_extension or "mp3"
    else:
        extension = "unknown"

    if dot:
        extension = "." + extension
    return extension


async def send_message_by_language(
    client: pyrogram.client.Client,
    language: Language,
    chat_id: Union[int, str],
    reply_to_message_id: int,
    language_str: List[str],
):
    """Record download status"""
    msg = language_str[language.value - 1]

    return await client.send_message(
        chat_id, msg, reply_to_message_id=reply_to_message_id
    )


async def download_thumbnail(
    client: pyrogram.Client,
    temp_path: str,
    message: pyrogram.types.Message,
):
    """Downloads the thumbnail of a video message to a temporary file.

    Args:
        client: A Pyrogram client instance.
        temp_path: The path to a temporary directory where the thumbnail file
                   will be stored.
        message: A Pyrogram Message object representing the video message.

    Returns:
        A string representing the path of the thumbnail file, or None if the
        download failed.

    Raises:
        ValueError: If the downloaded thumbnail file size doesn't match the
                    expected file size.
    """
    thumbnail_file = None
    if message.video.thumbs:
        message = await fetch_message(client, message)
        thumbnail = message.video.thumbs[0] if message.video.thumbs else None
        unique_name = os.path.join(
            temp_path,
            "thumbnail",
            f"thumb-{int(time.time())}-{secrets.token_hex(8)}.jpg",
        )

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                thumbnail_file = await client.download_media(
                    thumbnail, file_name=unique_name
                )

                if os.path.getsize(thumbnail_file) == thumbnail.file_size:
                    break

                raise ValueError(
                    f"Thumbnail file size is {os.path.getsize(thumbnail_file)}"
                    f" bytes, actual {thumbnail.file_size}: {thumbnail_file}"
                )

            except Exception as e:
                if attempt == max_attempts:
                    logger.exception(
                        f"Failed to download thumbnail after {max_attempts}"
                        f" attempts: {e}"
                    )
                else:
                    message = await fetch_message(client, message)
                    logger.warning(
                        f"Attempt {attempt} to download thumbnail failed: {e}"
                    )
                    # Wait 2 seconds before retrying
                    await asyncio.sleep(2)

                thumbnail = None
                thumbnail_file = None
    return thumbnail_file


async def upload_telegram_chat(
    client: pyrogram.Client,
    upload_user: pyrogram.Client,
    app: Application,
    node: TaskNode,
    message: pyrogram.types.Message,
    download_status: DownloadStatus,
    file_name: str = None,
):
    """Upload telegram chat"""
    # upload telegram
    if node.upload_telegram_chat_id:
        if download_status is DownloadStatus.SkipDownload and message.media:
            if message.media_group_id:
                await proc_cache_forward(client, node, message, True)
            return

        if download_status is DownloadStatus.SuccessDownload or (
            download_status is DownloadStatus.SkipDownload and not message.media
        ):
            try:
                await upload_telegram_chat_message(
                    client,
                    upload_user,
                    app,
                    node,
                    message,
                    file_name,
                )
            except Exception as e:
                logger.exception(f"Upload file {file_name} error: {e}")
            finally:
                if file_name and app.after_upload_telegram_delete:
                    os.remove(file_name)

            # forward text
            # FIXME: fix upload text
            # if (
            #     download_status is DownloadStatus.SkipDownload
            #     and message.text
            #     and bot
            # ):
            #     await upload_telegram_chat(
            #         client, app, node.upload_telegram_chat_id, message, file_name
            #     )


async def upload_telegram_chat_message(
    client: pyrogram.Client,
    upload_user: pyrogram.Client,
    app: Application,
    node: TaskNode,
    message: pyrogram.types.Message,
    file_name: str = None,
) -> ForwardStatus:
    """See upload telegram_chat"""
    forward_status = ForwardStatus.FailedForward
    max_attempts = 3
    for _ in range(1, max_attempts + 1):
        try:
            forward_status = await _upload_telegram_chat_message(
                client, upload_user, app, node, message, file_name
            )
            break
        except pyrogram.errors.exceptions.flood_420.FloodWait as wait_err:
            await asyncio.sleep(wait_err.value * 2)
            logger.warning(
                "Upload Message[{}]: FlowWait {}", message.id, wait_err.value
            )
        except Exception as e:
            logger.exception(f"Upload file {file_name} error: {e}")
            return ForwardStatus.FailedForward

    if forward_status != ForwardStatus.CacheForward:
        node.stat_forward(forward_status)
    return forward_status


# pylint: disable=R0912
async def _upload_signal_message(
    client: pyrogram.Client,
    upload_user: pyrogram.Client,
    app: Application,
    node: TaskNode,
    upload_telegram_chat_id: Union[int, str, None],
    message: pyrogram.types.Message,
    file_name: Optional[str],
    caption: Optional[str] = None,
):
    """
    Uploads a video or message to a Telegram chat.

    Parameters:
        client (pyrogram.Client): The pyrogram client.
        upload_telegram_chat_id (Union[int, str]): The ID of the chat to upload to.
        message (pyrogram.types.Message): The message to upload.
        file_name (str): The name of the file to upload.
    """
    ui_file_name = file_name
    if file_name:
        ui_file_name = (
            f"****{os.path.splitext(file_name)[-1]}"
            if app.hide_file_name
            else file_name
        )

    if message.video:
        # Download thumbnail
        thumbnail_file = await download_thumbnail(client, app.temp_save_path, message)
        try:
            # TODO(tangyoha): add more log when upload video more than 2000MB failed
            # Send video to the destination chat
            if node.reply_to_message:
                await node.reply_to_message.reply_video(
                    file_name,
                    caption=caption,
                    message_thread_id=node.topic_id,
                    thumb=thumbnail_file,
                    width=message.video.width,
                    height=message.video.height,
                    duration=message.video.duration,
                    parse_mode=pyrogram.enums.ParseMode.HTML,
                )
            else:
                await upload_user.send_video(
                    upload_telegram_chat_id,
                    file_name,
                    thumb=thumbnail_file,
                    width=message.video.width,
                    height=message.video.height,
                    duration=message.video.duration,
                    caption=caption,
                    parse_mode=pyrogram.enums.ParseMode.HTML,
                    progress=update_upload_stat,
                    progress_args=(
                        message.id,
                        ui_file_name,
                        time.time(),
                        node,
                        upload_user,
                    ),
                    message_thread_id=node.topic_id,
                )
        except Exception as e:
            raise e
        finally:
            if thumbnail_file:
                os.remove(str(thumbnail_file))

    elif message.photo:
        if node.reply_to_message:
            await node.reply_to_message.reply_photo(
                file_name,
                caption=caption,
                message_thread_id=node.topic_id,
            )
        else:
            await upload_user.send_photo(
                upload_telegram_chat_id,
                file_name,
                caption=caption,
                progress=update_upload_stat,
                progress_args=(
                    message.id,
                    ui_file_name,
                    time.time(),
                    node,
                    upload_user,
                ),
                message_thread_id=node.topic_id,
            )
    elif message.document:
        if node.reply_to_message:
            await node.reply_to_message.reply_document(
                file_name,
                caption=caption,
                message_thread_id=node.topic_id,
            )
        else:
            await upload_user.send_document(
                upload_telegram_chat_id,
                file_name,
                caption=caption,
                progress=update_upload_stat,
                progress_args=(
                    message.id,
                    ui_file_name,
                    time.time(),
                    node,
                    upload_user,
                ),
                message_thread_id=node.topic_id,
            )
    elif message.voice:
        if node.reply_to_message:
            await node.reply_to_message.reply_voice(
                file_name,
                caption=caption,
                message_thread_id=node.topic_id,
            )
        else:
            await upload_user.send_voice(
                upload_telegram_chat_id,
                file_name,
                caption=caption,
                progress=update_upload_stat,
                progress_args=(
                    message.id,
                    ui_file_name,
                    time.time(),
                    node,
                    upload_user,
                ),
                message_thread_id=node.topic_id,
            )
    elif message.video_note:
        if node.reply_to_message:
            await node.reply_to_message.reply_video_note(
                file_name,
                caption=caption,
                message_thread_id=node.topic_id,
            )
        else:
            await upload_user.send_video_note(
                upload_telegram_chat_id,
                file_name,
                caption=caption,
                progress=update_upload_stat,
                progress_args=(
                    message.id,
                    ui_file_name,
                    time.time(),
                    node,
                    upload_user,
                ),
                message_thread_id=node.topic_id,
            )
    elif message.text:
        if node.reply_to_message:
            await node.reply_to_message.reply(
                message.text, message_thread_id=node.topic_id
            )
        else:
            await upload_user.send_message(
                upload_telegram_chat_id,
                message.text,
                message_thread_id=node.topic_id,
            )


async def _upload_telegram_chat_message(
    client: pyrogram.Client,
    upload_user: pyrogram.Client,
    app: Application,
    node: TaskNode,
    message: pyrogram.types.Message,
    file_name: str = None,
):
    """
    Uploads a Telegram chat message to the destination chat.

    Args:
        client (pyrogram.Client): The client used to interact with the Telegram API.
        upload_user (pyrogram.Client): The client used to upload the message.
        app (Application): The application instance.
        node (TaskNode): The task node associated with the message.
        message (pyrogram.types.Message): The Telegram chat message to be uploaded.
        file_name (str): The name of the file to be uploaded.

    Returns:
        None
    """
    await app.forward_limit_call.wait(node)

    caption = _clean_caption(message.caption or "")
    caption_entities = message.caption_entities

    # Convert caption and caption_entities to markdown format
    if caption and caption_entities:
        caption = pyrogram.parser.Parser.unparse(caption, caption_entities, True)

    max_caption_length = 4096 if client.me and client.me.is_premium else 1024
    # proc caption MEDIA_CAPTION_TOO_LONG
    if caption and len(caption) > max_caption_length:
        caption = caption[:max_caption_length]

    if not message.media_group_id:
        if not node.has_protected_content:
            if node.reply_to_message:
                if message.text:
                    await node.reply_to_message.reply(
                        message.text,
                        message_thread_id=node.topic_id,
                    )
                elif message.photo:
                    await node.reply_to_message.reply_photo(
                        message.photo.file_id,
                        caption=caption,
                        message_thread_id=node.topic_id,
                    )
                elif message.video:
                    await node.reply_to_message.reply_video(
                        message.video.file_id,
                        caption=caption,
                        message_thread_id=node.topic_id,
                    )
                elif message.document:
                    await node.reply_to_message.reply_document(
                        message.document.file_id,
                        caption=caption,
                        message_thread_id=node.topic_id,
                    )
                elif message.audio:
                    await node.reply_to_message.reply_audio(
                        message.audio.file_id,
                        caption=caption,
                        message_thread_id=node.topic_id,
                    )
            else:
                if message.video and node.forward_video_screenshot:
                    thumb_path = await download_thumbnail(
                        client, app.temp_save_path, message
                    )
                    if not caption:
                        caption = "视频详见评论区👇"
                    else:
                        caption = caption + "\n\n视频详见评论区👇"
                    if thumb_path:
                        photo_msg = await upload_user.send_photo(
                            node.upload_telegram_chat_id,
                            thumb_path,
                            caption=caption,
                            message_thread_id=node.topic_id,
                        )
                        os.remove(thumb_path)
                    else:
                        photo_msg = await upload_user.send_message(
                            node.upload_telegram_chat_id,
                            caption if caption else "📹 Video",
                            message_thread_id=node.topic_id,
                        )
                    try:
                        disc = await _get_discussion_message_retry(
                            client, node.upload_telegram_chat_id, photo_msg.id
                        )
                        await message.copy(
                            disc.chat.id,
                            caption="",
                            reply_to_message_id=disc.id,
                            message_thread_id=node.topic_id,
                        )
                    except Exception:
                        await message.copy(
                            node.upload_telegram_chat_id,
                            caption="",
                            reply_to_message_id=photo_msg.id,
                            message_thread_id=node.topic_id,
                        )
                else:
                    await forward_messages(
                        client,
                        node.upload_telegram_chat_id,
                        node.chat_id,
                        message.id,
                        drop_author=True,
                        topic_id=node.topic_id,
                        caption=caption,
                    )
        else:
            await _upload_signal_message(
                client,
                upload_user,
                app,
                node,
                node.upload_telegram_chat_id,
                message,
                file_name,
                caption,
            )
        return ForwardStatus.SuccessForward

    return await forward_multi_media(
        client, upload_user, app, node, message, caption, file_name
    )


# pylint: disable=R0912
async def forward_multi_media(
    client: pyrogram.Client,
    _: pyrogram.Client,
    app: Application,
    node: TaskNode,
    message: pyrogram.types.Message,
    caption: str = None,
    file_name: str = None,
):
    """Forward multi media by cache"""
    caption = _clean_caption(message.caption or "")
    caption_entities = message.caption_entities
    if not caption:
        caption = app.get_caption_name(node.chat_id, message.media_group_id)
        caption_entities = app.get_caption_entities(
            node.chat_id, message.media_group_id
        )

    # Convert caption and caption_entities to markdown format
    if caption and caption_entities:
        caption = pyrogram.parser.Parser.unparse(caption, caption_entities, True)

    max_caption_length = 4096 if client.me and client.me.is_premium else 1024
    # proc caption MEDIA_CAPTION_TOO_LONG
    if caption and len(caption) > max_caption_length:
        caption = caption[:max_caption_length]

    media_obj = get_media_obj(message, file_name, caption)
    if not node.has_protected_content:
        media = getattr(message, message.media.value)
        if not media:
            return ForwardStatus.SkipForward
        media_obj.media = media.file_id if media else ""

    if not node.media_group_ids.get(message.media_group_id):
        node.media_group_ids[message.media_group_id] = {}

    if not node.media_group_ids[message.media_group_id]:
        media_group = await get_media_group_with_retry(
            client, node.chat_id, message.id, 5
        )
        if not media_group:
            logger.error("Get Media Group Error! message id: {}", message.id)
            return ForwardStatus.FailedForward

        for it in media_group:
            node.media_group_ids[message.media_group_id][it.id] = None
            node.upload_status[message.id] = None

    if not node.media_group_ids[message.media_group_id][message.id]:
        node.upload_status[message.id] = UploadStatus.Uploading
        try:
            ui_file_name = file_name
            if file_name:
                ui_file_name = (
                    f"****{os.path.splitext(file_name)[-1]}"
                    if app.hide_file_name
                    else file_name
                )
                media_obj.thumb = (
                    await download_thumbnail(client, app.temp_save_path, message)
                    if message.video
                    else None
                )

            _media = await cache_media(
                client,
                node.upload_telegram_chat_id,  # type: ignore
                media_obj,
                progress=update_upload_stat,
                progress_args=(
                    message.id,
                    ui_file_name,
                    time.time(),
                    node,
                    client,
                ),
            )
        except Exception as e:
            logger.exception(f"{e}")
            node.upload_status[message.id] = UploadStatus.FailedUpload
        finally:
            if file_name and message.video and media_obj.thumb:
                os.remove(str(media_obj.thumb))

        if node.upload_status[message.id] == UploadStatus.FailedUpload:
            return ForwardStatus.FailedForward

        node.media_group_ids[message.media_group_id][message.id] = _media
        node.upload_status[message.id] = UploadStatus.SuccessUpload

    return await proc_cache_forward(client, node, message, bool(file_name))


async def proc_cache_forward(
    client: pyrogram.Client,
    node: TaskNode,
    message: pyrogram.types.Message,
    check_download_status: bool,
):
    """proc other cache forward"""
    if not node.media_group_ids:
        return
    for key in node.media_group_ids[message.media_group_id].keys():
        download_status = node.download_status.get(key, DownloadStatus.Downloading)
        if (
            node.skip_msg_id(key)
            or download_status is DownloadStatus.SkipDownload
            or download_status is DownloadStatus.FailedDownload
        ):
            continue
        if (
            check_download_status and DownloadStatus.Downloading == download_status
        ) or UploadStatus.Uploading == node.upload_status.get(
            key, UploadStatus.Uploading
        ):
            return ForwardStatus.CacheForward

    multi_media: List[pyrogram.raw.types.InputSingleMedia] = []

    for it in node.media_group_ids[message.media_group_id]:
        if node.media_group_ids[message.media_group_id][it]:
            if multi_media:
                node.media_group_ids[message.media_group_id][it].message = ""
            multi_media.append(node.media_group_ids[message.media_group_id][it])

    forward_status = ForwardStatus.SuccessForward

    reply_to_message_id = None
    message_thread_id = node.topic_id
    upload_telegram_chat_id = node.upload_telegram_chat_id
    if node.reply_to_message:
        if node.reply_to_message.chat.type != pyrogram.enums.ChatType.PRIVATE:
            reply_to_message_id = node.reply_to_message.id
        message_thread_id = node.reply_to_message.message_thread_id
        upload_telegram_chat_id = node.reply_to_message.chat.id
    if not await send_media_group_v2(
        client,
        upload_telegram_chat_id,  # type: ignore
        multi_media,
        message_thread_id=message_thread_id,
        reply_to_message_id=reply_to_message_id,
    ):
        forward_status = ForwardStatus.FailedForward

    node.stat_forward(forward_status, len(multi_media))

    node.media_group_ids.pop(message.media_group_id)
    return ForwardStatus.CacheForward


def record_download_status(func):
    """Record download status"""

    @wraps(func)
    async def inner(
        client: pyrogram.client.Client,
        message: pyrogram.types.Message,
        media_types: List[str],
        file_formats: dict,
        node: TaskNode,
    ):
        if _download_cache[(node.chat_id, message.id)] is DownloadStatus.Downloading:
            return DownloadStatus.Downloading, None

        _download_cache[(node.chat_id, message.id)] = DownloadStatus.Downloading

        status, file_name = await func(client, message, media_types, file_formats, node)

        _download_cache[(node.chat_id, message.id)] = status

        return status, file_name

    return inner


async def report_bot_download_status(
    client: pyrogram.Client,
    node: TaskNode,
    download_status: DownloadStatus,
    download_size: int = 0,
):
    """
    Sends a message with the current status of the download bot.

    Parameters:
        client (pyrogram.Client): The client instance.
        node (TaskNode): The download task node.
        download_status (DownloadStatus): The current download status.

    Returns:
        None
    """
    node.stat(download_status)
    node.total_download_byte += download_size
    await report_bot_status(client, node)


async def report_bot_forward_status(
    client: pyrogram.Client,
    node: TaskNode,
    status: ForwardStatus,
):
    """
    Sends a message with the current status of the download bot.

    Parameters:
        client (pyrogram.Client): The client instance.
        node (TaskNode): The download task node.
        status (ForwardStatus): The current forward status.

    Returns:
        None
    """
    node.stat_forward(status)
    await report_bot_status(client, node)


async def report_bot_status(
    client: pyrogram.Client,
    node: TaskNode,
    immediate_reply=False,
):
    """see _report_bot_status"""
    try:
        return await _report_bot_status(client, node, immediate_reply)
    except Exception as e:
        logger.warning(f"report_bot_status error: {e}")


async def _report_bot_status(
    client: pyrogram.Client,
    node: TaskNode,
    immediate_reply=False,
):
    """
    Sends a message with the current status of the download bot.

    Parameters:
        client (pyrogram.Client): The client instance.
        node (TaskNode): The download task node.
        immediate_reply(bool): Immediate reply

    Returns:
        None
    """
    if not node.reply_message_id or not node.bot:
        return

    if immediate_reply or node.can_reply():
        if node.upload_telegram_chat_id:
            node.forward_msg_detail_str = (
                f"\n🔄 {_t('Forward')}\n"
                f"├─ 📁 {_t('Total')}: {node.total_forward_task}\n"
                f"├─ ✅ {_t('Success')}: {node.success_forward_task}\n"
                f"├─ ❌ {_t('Failed')}: {node.failed_forward_task}\n"
                f"└─ ⏩ {_t('Skipped')}: {node.skip_forward_task}\n"
            )

        upload_msg_detail_str: str = ""

        if node.upload_success_count:
            upload_msg_detail_str = (
                f"\n☁️ {_t('Upload')}\n"
                f"└─ ✅ {_t('Success')}: {node.upload_success_count}\n"
            )

        for idx, value in node.cloud_drive_upload_stat_dict.items():
            if value.transferred == value.total:
                continue

            temp_file_name = truncate_filename(os.path.basename(value.file_name), 10)
            upload_msg_detail_str += (
                f" ├─ 🆔 {_t('Message ID')}: {idx}\n"
                f" │   ├─ 📁 : {temp_file_name}\n"
                f" │   ├─ 📏 : {value.total}\n"
                f" │   ├─ ⏫ : {value.speed}\n"
                f" │   └─ 📊 : ["
                f'{create_progress_bar(int(value.percentage.split("%")[0]))}]'
                f" ({value.percentage})%\n"
            )

        download_result_str = ""
        download_result = get_download_result()
        if node.chat_id in download_result:
            messages = download_result[node.chat_id]
            for idx, value in messages.items():
                task_id = value["task_id"]
                if task_id != node.task_id or value["down_byte"] == value["total_size"]:
                    continue

                temp_file_name = truncate_filename(
                    os.path.basename(value["file_name"]), 10
                )
                progress = int(value["down_byte"] / max(value["total_size"], 1) * 100)
                download_result_str += (
                    f" ├─ 🆔 {_t('Message ID')}: {idx}\n"
                    f" │   ├─ 📁 : {temp_file_name}\n"
                    f" │   ├─ 📏 : {format_byte(value['total_size'])}\n"
                    f" │   ├─ ⏬ : {format_byte(value['download_speed'])}/s\n"
                    f" │   └─ 📊 : [{create_progress_bar(progress)}]"
                    f" ({progress}%)\n"
                )

            if download_result_str:
                download_result_str = (
                    f"\n📥 {_t('Download Progresses')}:\n" + download_result_str
                )

        upload_result_str = ""
        for idx, value in node.upload_stat_dict.items():
            if value.total_size == value.upload_size:
                continue

            temp_file_name = truncate_filename(os.path.basename(value.file_name), 10)
            progress = int(value.upload_size / max(value.total_size, 1) * 100)
            upload_result_str += (
                f" ├─ 🆔 {_t('Message ID')}: {idx}\n"
                f" │   ├─ 📁 : {temp_file_name}\n"
                f" │   ├─ 📏 : {format_byte(value.total_size)}\n"
                f" │   ├─ ⏫ : {format_byte(value.upload_speed)}/s\n"
                f" │   └─ 📊 : [{create_progress_bar(progress)}]"
                f" ({progress}%)\n"
            )

        if upload_result_str:
            upload_result_str = f"\n📤 {_t('Upload Progresses')}:\n" + upload_result_str

        new_msg_str = (
            f"`\n"
            f"🆔 task id: {node.task_id}\n"
            f"📥 {_t('Downloading')}: {format_byte(node.total_download_byte)}\n"
            f"├─ 📁 {_t('Total')}: {node.total_download_task}\n"
            f"├─ ✅ {_t('Success')}: {node.success_download_task}\n"
            f"├─ ❌ {_t('Failed')}: {node.failed_download_task}\n"
            f"└─ ⏩ {_t('Skipped')}: {node.skip_download_task}\n"
            f"{node.forward_msg_detail_str}"
            f"{upload_msg_detail_str}"
            f"{upload_result_str}"
            f"{download_result_str}"
            f"{node.forward_code_progress_str}\n`"
        )

        if new_msg_str != node.last_edit_msg:
            node.last_edit_msg = new_msg_str
            await client.edit_message_text(
                node.from_user_id,
                node.reply_message_id,
                new_msg_str,
                parse_mode=pyrogram.enums.ParseMode.MARKDOWN,
            )


def set_max_concurrent_transmissions(
    client: pyrogram.Client, max_concurrent_transmissions: int
):
    """Set maximum concurrent transmissions"""
    if getattr(client, "max_concurrent_transmissions", None):
        client.max_concurrent_transmissions = max_concurrent_transmissions
        client.save_file_semaphore = asyncio.Semaphore(
            client.max_concurrent_transmissions
        )
        client.get_file_semaphore = asyncio.Semaphore(
            client.max_concurrent_transmissions
        )


async def fetch_message(client: pyrogram.Client, message: pyrogram.types.Message):
    """
    This function retrieves a message from a specified chat using the Pyrogram library.
     Args:
        client (pyrogram.Client): A client instance created using Pyrogram.
        message (pyrogram.types.Message): A message instance returned from Pyrogram.
     Returns:
        pyrogram.types.Message: A message object retrieved from the specified chat.
    """
    return await client.get_messages(
        chat_id=message.chat.id,
        message_ids=message.id,
    )


async def retry(func: Callable, args: tuple = (), max_attempts=3, wait_second=15):
    """
    Asynchronously retries the provided function
    a specified number of times with a specified wait time between retries.

    :param func: The function to be retried.
    :param args: The arguments to be passed to the function.
    :param max_attempts: The maximum number of attempts to retry the function.
        Defaults to 3.
    :param wait_second: The wait time in seconds between each retry attempt.
        Defaults to 15.

    :return: The result of the function
    if it succeeds within the maximum number of attempts, otherwise None.
    """

    for _ in range(1, max_attempts + 1):
        try:
            return await func(*args)
        except pyrogram.errors.exceptions.flood_420.FloodWait as wait_err:
            logger.warning("bad call retry: FlowWait {}", wait_err.value)
            await asyncio.sleep(wait_err.value)
        except Exception as e:
            logger.exception("Error: {}", e)
            await asyncio.sleep(wait_second)

    logger.error("Failed after {} attempts", max_attempts)
    return None


async def get_media_group_with_retry(
    client: pyrogram.Client,
    chat_id: Union[int, str],
    message_id: int,
    max_attempts: int = 3,
    wait_second: int = 15,
):
    """
    get_media_group_with_retry
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return await client.get_media_group(chat_id, message_id)
        except Exception as e:
            if attempt == max_attempts:
                logger.error("Failed Get Media Group[{}]", message_id)
                return types.List()

            logger.exception("Get Message[{}]: Error {}", message_id, e)
            await asyncio.sleep(wait_second)
    return types.List()


async def check_user_permission(
    client: pyrogram.Client, user_id: Union[int, str], chat_id: Union[int, str]
) -> bool:
    """
    Check if the user has permission to send videos in the group.

    Args:
        client (pyrogram.Client): A client instance created using Pyrogram.
        user_id (Union[int, str]): User Id
        chat_id (Union[int, str]): Chat Id

     Returns:
        if can_send_media_messages return True
    """
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member and (
            not member.permissions or member.permissions.can_send_media_messages
        )
    except Exception:
        # logger.exception(e)
        pass

    return False


def set_meta_data(
    meta_data: MetaData, message: pyrogram.types.Message, caption: str = None
):
    """Get all meta data"""
    # message
    meta_data.message_date = getattr(message, "date", None)
    if caption:
        meta_data.message_caption = caption
    else:
        meta_data.message_caption = getattr(message, "caption", None) or ""
    meta_data.message_id = getattr(message, "id", None)

    from_user = getattr(message, "from_user")
    meta_data.sender_id = from_user.id if from_user else 0
    meta_data.sender_name = (from_user.username if from_user else "") or ""
    meta_data.reply_to_message_id = getattr(
        message, "reply_to_message_id", 1
    )  # 1 for General

    meta_data.message_thread_id = getattr(message, "message_thread_id", 1)
    # media
    for kind in meta_data.AVAILABLE_MEDIA:
        media_obj = getattr(message, kind, None)
        if media_obj is not None:
            meta_data.media_type = kind
            break
    else:
        return
    meta_data.media_file_name = getattr(media_obj, "file_name", None) or ""
    meta_data.media_file_size = getattr(media_obj, "file_size", None)
    meta_data.media_width = getattr(media_obj, "width", None)
    meta_data.media_height = getattr(media_obj, "height", None)
    meta_data.media_duration = getattr(media_obj, "duration", None)
    meta_data.file_extension = get_extension(
        media_obj.file_id, getattr(media_obj, "mime_type", ""), False
    )


async def parse_link(client: pyrogram.Client, link_str: str):
    """Parse link"""
    link = extract_info_from_link(link_str)
    if link.comment_id:
        chat = await client.get_chat(link.group_id)
        if chat:
            return chat.linked_chat.id, link.comment_id, link.topic_id

    return link.group_id, link.post_id, link.topic_id


async def update_cloud_upload_stat(
    transferred: str,
    total: str,
    percentage: str,
    speed: str,
    eta: str,
    node: TaskNode,
    message_id: int,
    file_name: str,
):
    """
    Update the cloud upload statistics with the given information.

    Args:
        transferred (str): The amount of data transferred.
        total (str): The total size of the file.
        percentage (str): The percentage of the file uploaded.
        speed (str): The upload speed.
        eta (str): The estimated time of arrival for the upload to complete.
        node (TaskNode): The task node associated with the upload.
        message_id (int): The ID of the message.
        file_name (str): The name of the file being uploaded.

    Returns:
        None
    """
    node.cloud_drive_upload_stat_dict[message_id] = CloudDriveUploadStat(
        file_name=file_name,
        transferred=transferred,
        total=total,
        percentage=percentage,
        speed=speed,
        eta=eta,
    )


async def update_upload_stat(
    upload_size: int,
    total_size: int,
    message_id: int,
    file_name: str,
    start_time: float,
    node: TaskNode,
    client: pyrogram.Client,
):
    """update_upload_status"""
    cur_time = time.time()

    if node.is_stop_transmission:
        client.stop_transmission()

    # TODO(tyh): web control upload stop

    if node.upload_stat_dict.get(message_id):
        upload_stat = node.upload_stat_dict[message_id]

        if cur_time - upload_stat.last_stat_time >= 1.0:
            upload_stat.upload_speed = max(
                int(
                    (upload_size - upload_stat.upload_size)
                    / (cur_time - upload_stat.last_stat_time)
                ),
                0,
            )
            upload_stat.last_stat_time = cur_time
            upload_stat.upload_size = upload_size

        node.upload_stat_dict[message_id] = upload_stat
    else:
        upload_stat = UploadProgressStat(
            file_name=file_name,
            total_size=total_size,
            upload_size=upload_size,
            start_time=start_time,
            last_stat_time=cur_time,
            upload_speed=upload_size / (cur_time - start_time),
        )
        node.upload_stat_dict[message_id] = upload_stat


    if message_id not in node.cloud_drive_upload_stat_dict:
        node.cloud_drive_upload_stat_dict[message_id] = CloudDriveUploadStat(
            file_name=file_name, transferred="", total="",
            percentage="", speed="", eta="")
    cds = node.cloud_drive_upload_stat_dict[message_id]
    total_sz = max(total_size, 1)
    cds.transferred = str(upload_size)
    cds.total = str(total_sz)
    cds.percentage = f"{int(upload_size/total_sz*100)}%"
    cds.speed = f"{max(upload_stat.upload_speed, 0)/1048576:.1f} MB/s"
    if not hasattr(node, "_rpt_tick"):
        node._rpt_tick = 0
    node._rpt_tick += 1
    if node._rpt_tick % 3 == 0:
        await report_bot_status(node.bot, node, immediate_reply=True)

# pylint: enable=W0201
class HookSession(pyrogram.session.Session):
    """Hook Session"""

    def start_timeout(self: pyrogram.session.Session, start_timeout: int):
        """
        Set the start timeout for the session.

        Args:
            start_timeout (int): The start timeout value in seconds.

        Returns:
            None
        """
        self.START_TIMEOUT = start_timeout


# pylint: disable=all
class HookClient(pyrogram.Client):
    """Hook Client"""

    # pylint: disable=R0901
    START_TIME_OUT = 60

    def __init__(self, name: str, **kwargs):
        if "start_timeout" in kwargs:
            value = kwargs.get("start_timeout")
            if value:
                self.START_TIME_OUT = value
            kwargs.pop("start_timeout")

        super().__init__(name, **kwargs)

    async def connect(
        self,
    ) -> bool:
        """
        Connects the client to the server.

        Returns:
            bool: True if the client successfully
                connects to the server, False otherwise.

        Raises:
            ConnectionError: If the client is already connected.

        """
        if self.is_connected:  # type: ignore
            raise ConnectionError("Client is already connected")

        await self.load_session()

        self.session = HookSession(
            self,
            await self.storage.dc_id(),
            await self.storage.auth_key(),
            await self.storage.test_mode(),
        )
        self.session.start_timeout(self.START_TIME_OUT)

        await self.session.start()

        self.is_connected = True

        return bool(await self.storage.user_id())

    async def start(self):
        """
        Starts the client by performing necessary initialization steps.

        Returns:
            The initialized client instance.
        """
        is_authorized = await self.connect()

        try:
            if not is_authorized:
                await self.authorize()

            if not await self.storage.is_bot() and self.takeout:
                self.takeout_id = (
                    await self.invoke(
                        pyrogram.raw.functions.account.InitTakeoutSession()
                    )
                ).id
                logger.warning(f"Takeout session {self.takeout_id} initiated")

            await self.invoke(pyrogram.raw.functions.updates.GetState())
        except (Exception, KeyboardInterrupt):
            await self.disconnect()
            raise
        else:
            self.me = await self.get_me()
            await self.initialize()

            return self


# pylint: disable=R0914,R0913
async def forward_messages(
    client: pyrogram.Client,
    chat_id: Union[int, str, None],
    from_chat_id: Union[int, str],
    message_ids: Union[int, Iterable[int]],
    disable_notification: bool = None,
    schedule_date: datetime = None,
    protect_content: bool = None,
    drop_author: bool = None,
    topic_id: int = None,
    caption: str = None,
    caption_entities: List[pyrogram.types.MessageEntity] = None,
) -> Union["types.Message", List["types.Message"]]:
    """Forward messages of any kind."""

    is_iterable = not isinstance(message_ids, int)
    message_ids = list(message_ids) if is_iterable else [message_ids]  # type: ignore

    r = await client.invoke(
        pyrogram.raw.functions.messages.ForwardMessages(
            to_peer=await client.resolve_peer(chat_id),
            from_peer=await client.resolve_peer(from_chat_id),
            id=message_ids,
            silent=disable_notification or None,
            random_id=[client.rnd_id() for _ in message_ids],
            schedule_date=pyrogram.utils.datetime_to_timestamp(schedule_date),
            noforwards=protect_content,
            drop_author=drop_author,
            top_msg_id=topic_id,
        )
    )

    forwarded_messages = []

    users = {i.id: i for i in r.users}
    chats = {i.id: i for i in r.chats}

    for i in r.updates:
        if isinstance(
        i,
            (
                pyrogram.raw.types.UpdateNewMessage,
                pyrogram.raw.types.UpdateNewChannelMessage,
                pyrogram.raw.types.UpdateNewScheduledMessage,
            ),
        ):
            forwarded_messages.append(
                # pylint: disable=W0212
                await types.Message._parse(client, i.message, users, chats)
            )

    if caption and not is_iterable and forwarded_messages:
        try:
            await client.edit_message_caption(
                chat_id, forwarded_messages[0].id, caption=caption
            )
        except pyrogram.errors.exceptions.bad_request_400.MessageNotModified:
            pass
    return types.List(forwarded_messages) if is_iterable else forwarded_messages[0]


# === NEW: /forward_multi & /forward_album support ===


async def _get_discussion_message_retry(client, chat_id, message_id, retries: int = 4, delay: float = 2.0):
    """带重试获取主帖对应的讨论区(评论区)消息。

    两类失败都覆盖：
    1) 主帖刚发出、评论锚点尚未同步 → 重试给时间；
    2) 相册(媒体组)帖只有其中一条是讨论锚点，send_media_group 返回的第一条
       不一定是它，用错会 MSG_ID_INVALID → 用 get_media_group 取同相册全部
       消息 id，逐条尝试，直到命中锚点那条。
    重试用尽仍失败则记录日志后抛出，由调用方兜底。
    """
    last_exc = None
    candidate_ids = [message_id]
    tried_group = False
    for attempt in range(retries):
        for mid in candidate_ids:
            try:
                return await client.get_discussion_message(chat_id, mid)
            except Exception as e:
                last_exc = e
        # 首次失败即取相册全部消息 id，并在本轮立即尝试新增的那些（不等 sleep）
        if not tried_group:
            tried_group = True
            try:
                grp = await client.get_media_group(chat_id, message_id)
                ids = [m.id for m in grp]
                for mid in [i for i in ids if i not in candidate_ids]:
                    try:
                        return await client.get_discussion_message(chat_id, mid)
                    except Exception as e:
                        last_exc = e
                if ids:
                    candidate_ids = ids
            except Exception:
                pass
        if attempt < retries - 1:
            await asyncio.sleep(delay)
    logger.warning(
        f"get_discussion_message failed for post {message_id} in chat {chat_id} "
        f"after {retries} tries over ids {candidate_ids} ({last_exc}); "
        f"falling back to main channel"
    )
    raise last_exc


async def _copy_item(node, item, chat_id, **kwargs):
    """Copy a buffered message to target chat, tracking forward stats."""
    try:
        await item.copy(chat_id, **kwargs)
        node.stat_forward(ForwardStatus.SuccessForward)
    except Exception as e:
        logger.warning(f"copy failed for msg {item.id}: {e}")
        node.stat_forward(ForwardStatus.FailedForward)


async def finalize_forward_multi(client, app, node):
    """总调度: 根据 node 配置分发到对应子模式"""
    items = node.forward_multi_buffer
    if not items:
        return
    if node.forward_multi_mode:
        if node.forward_multi_single_thumb:
            await _flush_single_thumb(client, app, node, items)
        else:
            await _flush_multi_thumb(client, app, node, items)
    elif node.forward_album_mode:
        await _flush_album_mode(client, node, items)


async def process_multi_single_msg(client, app, node, item):
    """forward_multi 单条消息处理（无媒体组）
    视频 → 缩略图主帖 + 评论区视频；图片/其他 → 直接复制转发
    """
    if node.is_stop_transmission:
        return
    await app.forward_limit_call.wait(node)
    upload_client = node.upload_user or client

    if item.video:
        try:
            fresh = await client.get_messages(node.chat_id, item.id)
            if fresh and not getattr(fresh, "empty", False) and fresh.video:
                item = fresh
        except Exception:
            pass

        caption = _clean_caption(item.caption or "")
        if item.video:
            if not caption:
                caption = "视频详见评论区👇"
            elif len(caption) + len("\n\n视频详见评论区👇") <= 1024 and "视频详见评论区" not in caption:
                caption += "\n\n视频详见评论区👇"
        thumb_path = None
        if item.video.thumbs:
            thumb_path = await download_thumbnail(client, app.temp_save_path, item)

        if thumb_path:
            try:
                try:
                    photo_msg = await upload_client.send_photo(
                        node.upload_telegram_chat_id, thumb_path,
                        caption=caption, message_thread_id=node.topic_id or None)
                except Exception:
                    logger.warning(
                        f"upload_client send_photo failed for msg {item.id}, "
                        f"retrying with client")
                    photo_msg = await client.send_photo(
                        node.upload_telegram_chat_id, thumb_path,
                        caption=caption, message_thread_id=node.topic_id or None)
            except Exception:
                logger.warning(
                    f"Both clients failed to send thumbnail for msg {item.id}, "
                    f"skipping")
                node.failed_forward_ids.append(item.id)
                node.stat_forward(ForwardStatus.FailedForward)
                await report_bot_status(node.bot, node, immediate_reply=True)
                return
            finally:
                try:
                    os.remove(thumb_path)
                except Exception:
                    pass
        else:
            photo_msg = await upload_client.send_message(
                node.upload_telegram_chat_id,
                caption or "📹 视频详见评论区👇",
                message_thread_id=node.topic_id or None)

        try:
            disc = await _get_discussion_message_retry(
                client, node.upload_telegram_chat_id, photo_msg.id)
            await _copy_item(node, item, disc.chat.id,
                reply_to_message_id=disc.id, caption="")
        except Exception:
            await _copy_item(node, item, node.upload_telegram_chat_id,
                reply_to_message_id=photo_msg.id, caption="")
    else:
        await _copy_item(node, item, node.upload_telegram_chat_id)

    await report_bot_status(node.bot, node, immediate_reply=True)


async def process_multi_group(client, app, node, group_msgs, single_thumb: bool):
    """forward_multi 媒体组处理
    single_thumb=True : 第一项（图→图；视频→缩略图）发主帖，全部图片+视频→评论区
    single_thumb=False: 图片→相册主帖；纯视频组→回退单缩略图；视频→评论区
    """
    import pyrogram
    if node.is_stop_transmission:
        return
    await app.forward_limit_call.wait(node)
    upload_client = node.upload_user or client

    try:
        ids = [m.id for m in group_msgs]
        refreshed = await client.get_messages(node.chat_id, ids)
        if isinstance(refreshed, list):
            by_id = {m.id: m for m in refreshed if m and not getattr(m, "empty", False)}
            group_msgs = [by_id.get(m.id, m) for m in group_msgs]
    except Exception:
        pass

    photos = [m for m in group_msgs if m.photo]
    videos = [m for m in group_msgs if m.video]
    others = [m for m in group_msgs if not m.photo and not m.video]

    for msg in others:
        try:
            await _copy_item(node, msg, node.upload_telegram_chat_id)
        except Exception:
            node.failed_forward_ids.append(msg.id)

    if not photos and not videos:
        return

    caption = next((m.caption for m in group_msgs if m.caption), "") or ""
    caption = _clean_caption(caption)

    if not single_thumb and videos:
        suffix = "\n\n视频详见评论区👇"
        if not caption:
            caption = "视频详见评论区👇"
        elif len(caption) + len(suffix) <= 1024 and "视频详见评论区" not in caption:
            caption += suffix

    if single_thumb:
        # 单缩略图：看第一个元素是图片还是视频
        first = group_msgs[0]
        try:
            if first.photo:
                photo_msg = await upload_client.send_photo(
                    node.upload_telegram_chat_id, first.photo.file_id,
                    caption=caption, message_thread_id=node.topic_id or None)
            elif first.video:
                thumb_path = None
                if first.video.thumbs:
                    thumb_path = await download_thumbnail(client, app.temp_save_path, first)
                if thumb_path:
                    try:
                        photo_msg = await upload_client.send_photo(
                            node.upload_telegram_chat_id, thumb_path,
                            caption=caption, message_thread_id=node.topic_id or None)
                    finally:
                        try:
                            os.remove(thumb_path)
                        except Exception:
                            pass
                else:
                    photo_msg = await upload_client.send_message(
                        node.upload_telegram_chat_id,
                        caption or "📹 视频详见评论区👇",
                        message_thread_id=node.topic_id or None)
            else:
                return
        except Exception:
            logger.exception(f"Failed to send main post for single_thumb group")
            for m in group_msgs:
                node.failed_forward_ids.append(m.id)
            return  # no main post, can't continue

        # 全部图片+视频都进评论区
        all_media = photos + videos
        try:
            disc = await _get_discussion_message_retry(
                client, node.upload_telegram_chat_id, photo_msg.id)
            for msg in all_media:
                try:
                    await _copy_item(node, msg, disc.chat.id,
                        reply_to_message_id=disc.id, caption="")
                except Exception:
                    try:
                        await _copy_item(node, msg, node.upload_telegram_chat_id,
                            reply_to_message_id=photo_msg.id, caption="")
                    except Exception:
                        node.failed_forward_ids.append(msg.id)
        except Exception:
            for msg in all_media:
                try:
                    await _copy_item(node, msg, node.upload_telegram_chat_id,
                        reply_to_message_id=photo_msg.id, caption="")
                except Exception:
                    node.failed_forward_ids.append(msg.id)

    else:
        # 多缩略图模式：
        #   纯视频组  → 全部视频缩略图相册 → 主帖；全部视频 → 评论区
        #   图片+视频 → 仅图片相册 → 主帖；全部视频 → 评论区（不混入视频缩略图）
        # 视频缩略图 file_id 是 THUMBNAIL 类型，直接放 send_media_group 会被 Pyrogram
        # 校验拒绝；图片 file_id 是 PHOTO 类型可直接使用。
        if not photos:
            # 纯视频组：下载每个缩略图 → send_photo 暂存到用户 DM 获取 PHOTO file_id
            # → send_media_group 组成相册主帖；全部视频 → 评论区
            media_list: list = []
            staging_msgs: list = []

            for video in videos:
                if not (video.video and video.video.thumbs):
                    continue
                thumb_path = await download_thumbnail(client, app.temp_save_path, video)
                if not thumb_path:
                    continue
                try:
                    staged = await upload_client.send_photo(node.from_user_id, thumb_path)
                    staging_msgs.append(staged)
                    media_list.append(pyrogram.types.InputMediaPhoto(
                        media=staged.photo.file_id,
                        caption=caption if not media_list else ""))
                except Exception as e:
                    logger.warning(f"Failed to stage thumbnail for video {video.id}: {e}")
                finally:
                    try:
                        os.remove(thumb_path)
                    except Exception:
                        pass

            photo_msg = None
            try:
                try:
                    if not media_list:
                        photo_msg = await upload_client.send_message(
                            node.upload_telegram_chat_id,
                            caption or "📹 视频详见评论区👇",
                            message_thread_id=node.topic_id or None)
                    elif len(media_list) == 1:
                        photo_msg = await upload_client.send_photo(
                            node.upload_telegram_chat_id, media_list[0].media,
                            caption=caption, message_thread_id=node.topic_id or None)
                    else:
                        sent = await upload_client.send_media_group(
                            node.upload_telegram_chat_id, media_list,
                            message_thread_id=node.topic_id or None)
                        photo_msg = sent[0] if sent else None
                except Exception:
                    logger.exception(
                        f"Failed to send main post for pure video group")
                    for v in videos:
                        node.failed_forward_ids.append(v.id)
                    photo_msg = None
            finally:
                for msg in staging_msgs:
                    try:
                        await msg.delete()
                    except Exception:
                        pass

            if photo_msg and videos:
                comment_media = [
                    pyrogram.types.InputMediaVideo(
                        media=v.video.file_id, caption="",
                        width=v.video.width, height=v.video.height,
                        duration=v.video.duration, supports_streaming=True)
                    for v in videos]
                try:
                    disc = await _get_discussion_message_retry(
                        client, node.upload_telegram_chat_id, photo_msg.id)
                    await _send_media_batched(
                        client, disc.chat.id, comment_media, 10,
                        reply_to_message_id=disc.id,
                        message_thread_id=node.topic_id or None)
                    for v in videos:
                        node.stat_forward(ForwardStatus.SuccessForward)
                except Exception as e:
                    logger.warning(
                        f"pure-video comment send to discussion failed ({e}); "
                        f"falling back to main channel for videos {[v.id for v in videos]}")
                    try:
                        await _send_media_batched(
                            client, node.upload_telegram_chat_id, comment_media, 10,
                            reply_to_message_id=photo_msg.id,
                            message_thread_id=node.topic_id or None)
                        for v in videos:
                            node.stat_forward(ForwardStatus.SuccessForward)
                    except Exception:
                        for v in videos:
                            node.failed_forward_ids.append(v.id)
                            node.stat_forward(ForwardStatus.FailedForward)
        else:
            # 图片+视频组：仅图片相册 → 主帖；全部图片+视频 → 评论区
            # 图片 file_id 是 PHOTO 类型，可直接用于 send_media_group，无需暂存
            media_list: list = []
            for photo in photos:
                media_list.append(pyrogram.types.InputMediaPhoto(
                    media=photo.photo.file_id,
                    caption=caption if not media_list else ""))

            try:
                if not media_list:
                    photo_msg = await upload_client.send_message(
                        node.upload_telegram_chat_id,
                        caption or "📷 详见评论区👇",
                        message_thread_id=node.topic_id or None)
                elif len(media_list) == 1:
                    # 单图片：用 client（获取消息的客户端）发送，避免 file_reference
                    # 跨客户端不匹配导致 MEDIA_EMPTY
                    fid = media_list[0].media
                    if not isinstance(fid, str) or not fid:
                        logger.warning(f"Single photo has empty/invalid file_id")
                        photo_msg = await upload_client.send_message(
                            node.upload_telegram_chat_id,
                            caption or "📷 详见评论区👇",
                            message_thread_id=node.topic_id or None)
                    else:
                        try:
                            photo_msg = await upload_client.send_photo(
                                node.upload_telegram_chat_id, fid,
                                caption=caption,
                                message_thread_id=node.topic_id or None)
                        except Exception:
                            logger.warning(
                                f"upload_client send_photo failed, retrying with client")
                            photo_msg = await client.send_photo(
                                node.upload_telegram_chat_id, fid,
                                caption=caption,
                                message_thread_id=node.topic_id or None)
                else:
                    # 多图相册同样：upload_client 发失败时回退到 client
                    try:
                        sent = await upload_client.send_media_group(
                            node.upload_telegram_chat_id, media_list,
                            message_thread_id=node.topic_id or None)
                    except Exception:
                        logger.warning(
                            f"upload_client send_media_group failed, retrying with client")
                        sent = await client.send_media_group(
                            node.upload_telegram_chat_id, media_list,
                            message_thread_id=node.topic_id or None)
                    photo_msg = sent[0] if sent else None
            except Exception:
                logger.exception(
                    f"Failed to send photo album for mixed group")
                photo_msg = None

            if photo_msg and videos:
                # 仅视频进评论区：图片已在主帖相册。若把图片混进评论区媒体组，
                # 这张图片会导致 send_media_group 失败、被静默兜底 → 整条退到主频道
                # （CLAUDE.md 规范也是"图片不进评论区"）。纯图片组 videos 为空，
                # 不进此分支 → 评论区无内容（符合规范）。
                comment_media = [
                    pyrogram.types.InputMediaVideo(
                        media=v.video.file_id, caption="",
                        width=v.video.width, height=v.video.height,
                        duration=v.video.duration, supports_streaming=True)
                    for v in videos]
                try:
                    disc = await _get_discussion_message_retry(
                        client, node.upload_telegram_chat_id, photo_msg.id)
                    await _send_media_or_single(
                        client, disc.chat.id, comment_media,
                        reply_to_message_id=disc.id,
                        message_thread_id=node.topic_id or None)
                    for item in photos + videos:
                        node.stat_forward(ForwardStatus.SuccessForward)
                except Exception as e:
                    logger.warning(
                        f"mixed-group comment send to discussion failed ({e}); "
                        f"falling back to main channel for videos {[v.id for v in videos]}")
                    try:
                        await _send_media_or_single(
                            client, node.upload_telegram_chat_id, comment_media,
                            reply_to_message_id=photo_msg.id,
                            message_thread_id=node.topic_id or None)
                        for item in photos + videos:
                            node.stat_forward(ForwardStatus.SuccessForward)
                    except Exception:
                        for item in photos + videos:
                            node.failed_forward_ids.append(item.id)
                            node.stat_forward(ForwardStatus.FailedForward)
            elif not photo_msg:
                # 主帖完全失败 → 所有源消息ID记入失败
                for m in group_msgs:
                    node.failed_forward_ids.append(m.id)

    await report_bot_status(node.bot, node, immediate_reply=True)


async def _flush_single_thumb(client, app, node, items):
    """Mode A-单个: 1张缩略图帖 + 全部内容进评论区"""
    first = items[0]
    thumb = None
    if first.video:
        thumb = await download_thumbnail(client, app.temp_save_path, first)

    captions = [_clean_caption(c) for c in node.forward_multi_captions if c]
    caption = "\n---\n".join(captions[:3]) if captions else ""
    caption += f"\n\n共{len(items)}个素材\n\n视频详见评论区👇"

    if thumb:
        photo_msg = await client.send_photo(
            node.upload_telegram_chat_id, thumb,
            caption=caption, message_thread_id=node.topic_id)
        os.remove(thumb)
    else:
        photo_msg = await client.send_message(
            node.upload_telegram_chat_id, caption,
            message_thread_id=node.topic_id)

    # Batch-refresh all file references in one call to avoid stale file_id errors
    try:
        refreshed = await client.get_messages(node.chat_id, [m.id for m in items])
        if isinstance(refreshed, list):
            items = [f if (f and not getattr(f, 'empty', False)) else o
                     for f, o in zip(refreshed, items)]
    except Exception:
        pass

    try:
        disc = await _get_discussion_message_retry(
            client, node.upload_telegram_chat_id, photo_msg.id)
        for item in items:
            await _copy_item(node, item, disc.chat.id,
                reply_to_message_id=disc.id,
                message_thread_id=node.topic_id, caption="")
    except Exception:
        for item in items:
            await _copy_item(node, item, node.upload_telegram_chat_id,
                reply_to_message_id=photo_msg.id,
                message_thread_id=node.topic_id, caption="")
    await report_bot_status(node.bot, node, immediate_reply=True)


async def _flush_multi_thumb(client, app, node, items):
    """Mode A-多个: 媒体组缩略图相册帖 + 全部内容进评论区"""
    import pyrogram
    await report_bot_status(node.bot, node)
    media_list = []

    captions = [_clean_caption(c) for c in node.forward_multi_captions if c]
    combined = "\n---\n".join(captions[:3]) if captions else ""
    combined += f"\n\n共{len(items)}个素材\n\n视频详见评论区👇"

    # 先刷新 file_reference，避免缩略图 file_id 过期
    try:
        refreshed = await client.get_messages(node.chat_id, [m.id for m in items[:10]])
        if isinstance(refreshed, list):
            by_id = {m.id: m for m in refreshed if m and not getattr(m, 'empty', False)}
            fresh10 = [by_id.get(item.id, item) for item in items[:10]]
        else:
            fresh10 = items[:10]
    except Exception:
        fresh10 = items[:10]

    # 直接用 Telegram 已存储的缩略图 file_id，不下载不上传
    # 与 _flush_album_mode 中用 msg.photo.file_id 的做法一致
    for i, item in enumerate(fresh10):
        if item.video and item.video.thumbs:
            thumb_fid = item.video.thumbs[-1].file_id
            cap = combined if i == 0 else ""
            media_list.append(
                pyrogram.types.InputMediaPhoto(media=thumb_fid, caption=cap))

    upload_client = node.upload_user or client
    if not media_list:
        photo_msg = await upload_client.send_message(
            node.upload_telegram_chat_id, combined,
            message_thread_id=node.topic_id or None)
    elif len(media_list) == 1:
        photo_msg = await upload_client.send_photo(
            node.upload_telegram_chat_id, media_list[0].media,
            caption=combined, message_thread_id=node.topic_id or None)
    else:
        msgs = await upload_client.send_media_group(
            node.upload_telegram_chat_id, media_list,
            message_thread_id=node.topic_id or None)
        photo_msg = msgs[0] if msgs else None

    # 刷新全部 items 的 file_reference 后再复制到评论区
    try:
        refreshed_all = await client.get_messages(node.chat_id, [m.id for m in items])
        if isinstance(refreshed_all, list):
            items = [f if (f and not getattr(f, 'empty', False)) else o
                     for f, o in zip(refreshed_all, items)]
    except Exception:
        pass

    if photo_msg:
        try:
            disc = await _get_discussion_message_retry(
                client, node.upload_telegram_chat_id, photo_msg.id)
            for item in items:
                await _copy_item(node, item, disc.chat.id,
                    reply_to_message_id=disc.id,
                    message_thread_id=node.topic_id, caption="")
        except Exception:
            for item in items:
                await _copy_item(node, item, node.upload_telegram_chat_id,
                    reply_to_message_id=photo_msg.id,
                    message_thread_id=node.topic_id, caption="")
    await report_bot_status(node.bot, node, immediate_reply=True)


async def _flush_album_mode(client, node, items):
    """Mode B: 合并媒体项为媒体组(<=10/组)，文字单独转发"""
    import pyrogram
    temp_files = []
    captions = [_clean_caption(c) for c in node.forward_multi_captions if c]
    combined = "\n---\n".join(captions) if captions else "视频详见评论区👇"
    if captions:
        combined = "\n---\n".join(captions) + "\n\n视频详见评论区👇"

    media_items = [m for m in items if m.video or m.photo or m.document]
    text_items = [m for m in items if m not in media_items]

    for msg in text_items:
        await msg.copy(node.upload_telegram_chat_id,
            message_thread_id=node.topic_id, caption="")

    for batch_idx in range(0, len(media_items), 10):
        batch = media_items[batch_idx:batch_idx + 10]
        media_list = []
        for j, msg in enumerate(batch):
            cap = combined if j == 0 else ""
            if msg.video:
                try:
                    fresh = await client.get_messages(node.chat_id, msg.id)
                    if fresh and fresh.video:
                        msg = fresh
                except Exception:
                    pass
                try:
                    path = await asyncio.wait_for(
                        client.download_media(msg.video,
                            progress=update_upload_stat,
                            progress_args=(msg.id, os.path.basename(str(msg.video.file_id)),
                                time.time(), node, client)),
                        timeout=300)
                    temp_files.append(str(path))
                    if _needs_transcode(str(path)):
                        logger.info("Transcoding msg %s to H.264...", msg.id)
                        fixed = _transcode_video(str(path))
                        if fixed:
                            temp_files.append(fixed)
                            path = fixed
                            logger.info("Transcode msg %s complete", msg.id)
                    media_list.append(
                        pyrogram.types.InputMediaVideo(
                            media=str(path), caption=cap,
                            width=msg.video.width,
                            height=msg.video.height,
                            duration=msg.video.duration,
                            supports_streaming=True))
                    node.stat_forward(ForwardStatus.SuccessForward)
                    await report_bot_status(node.bot, node, immediate_reply=True)
                except asyncio.TimeoutError:
                    logger.warning(f"Download timeout for msg {msg.id}, skipping")
                    node.stat_forward(ForwardStatus.FailedForward)
                    await report_bot_status(node.bot, node, immediate_reply=True)
                    continue
                except Exception as e:
                    logger.error(f"Download failed for msg {msg.id}: {e}")
                    node.stat_forward(ForwardStatus.FailedForward)
                    await report_bot_status(node.bot, node, immediate_reply=True)
                    continue
            elif msg.photo:
                media_list.append(
                    pyrogram.types.InputMediaPhoto(
                        media=msg.photo.file_id))
                node.stat_forward(ForwardStatus.SuccessForward)
            elif msg.document:
                try:
                    fresh = await client.get_messages(node.chat_id, msg.id)
                    if fresh and fresh.document:
                        msg = fresh
                except Exception:
                    pass
                try:
                    path = await asyncio.wait_for(
                        client.download_media(msg.document,
                            progress=update_upload_stat,
                            progress_args=(msg.id, os.path.basename(str(msg.document.file_id)),
                                time.time(), node, client)),
                        timeout=300)
                    temp_files.append(str(path))
                    media_list.append(
                        pyrogram.types.InputMediaDocument(
                            media=str(path), caption=cap,
                            attributes=msg.document.attributes))
                    node.stat_forward(ForwardStatus.SuccessForward)
                    await report_bot_status(node.bot, node, immediate_reply=True)
                except asyncio.TimeoutError:
                    logger.warning(f"Download timeout for msg {msg.id}, skipping")
                    node.stat_forward(ForwardStatus.FailedForward)
                    await report_bot_status(node.bot, node, immediate_reply=True)
                    continue
                except Exception as e:
                    logger.error(f"Download failed for msg {msg.id}: {e}")
                    node.stat_forward(ForwardStatus.FailedForward)
                    await report_bot_status(node.bot, node, immediate_reply=True)
                    continue
        if len(media_list) == 1:
            # send_media_group 至少需要 2 项，单项用 send_video/send_document
            single = media_list[0]
            if isinstance(single, pyrogram.types.InputMediaVideo):
                await node.upload_user.send_video(
                    node.upload_telegram_chat_id, single.media,
                    caption=single.caption,
                    width=getattr(single, 'width', None),
                    height=getattr(single, 'height', None),
                    duration=getattr(single, 'duration', None),
                    supports_streaming=True,
                    message_thread_id=node.topic_id)
            elif isinstance(single, pyrogram.types.InputMediaDocument):
                await node.upload_user.send_document(
                    node.upload_telegram_chat_id, single.media,
                    caption=single.caption,
                    message_thread_id=node.topic_id)
            elif isinstance(single, pyrogram.types.InputMediaPhoto):
                await node.upload_user.send_photo(
                    node.upload_telegram_chat_id, single.media,
                    caption=single.caption,
                    message_thread_id=node.topic_id)
        elif len(media_list) > 1:
            await node.upload_user.send_media_group(
                node.upload_telegram_chat_id, media_list,
                message_thread_id=node.topic_id)

    for f in temp_files:
        try:
            os.remove(f)
        except Exception:
            pass

async def forward_screenshot_split_group(client, upload_user, app, node, group_msgs):
    """媒体组截图拆分，规则：
    - 纯视频  ：全部视频缩略图 → 主帖，全部视频 → 评论区
    - 图片+视频：第一张图片+全部视频缩略图 → 主帖，其余图片+全部视频 → 评论区
    - 纯图片  ：第一张图片 → 主帖，其余图片 → 评论区
    - 文字/文档：普通转发
    """
    await app.forward_limit_call.wait(node)

    # 刷新 file_reference
    try:
        ids = [m.id for m in group_msgs]
        refreshed = await client.get_messages(node.chat_id, ids)
        if isinstance(refreshed, list):
            by_id = {m.id: m for m in refreshed if m and not getattr(m, "empty", False)}
            group_msgs = [by_id.get(m.id, m) for m in group_msgs]
    except Exception:
        pass

    photos = [m for m in group_msgs if m.photo]
    videos = [m for m in group_msgs if m.video]
    others = [m for m in group_msgs if not m.photo and not m.video]

    # 文字 / 文档：普通转发
    for msg in others:
        await upload_telegram_chat_message(client, upload_user, app, node, msg)

    if not photos and not videos:
        return

    # 取原始文案（第一个有 caption 的消息）
    raw_caption = ""
    raw_entities = None
    for m in group_msgs:
        if m.caption:
            raw_caption = m.caption
            raw_entities = m.caption_entities
            break
    if raw_caption and raw_entities:
        caption = pyrogram.parser.Parser.unparse(raw_caption, raw_entities, True)
    else:
        caption = raw_caption or ""
    caption = _clean_caption(caption)

    max_len = 4096 if (client.me and client.me.is_premium) else 1024

    thumb_paths: list = []
    dst_post = None
    try:
        # ── 构建主帖内容 ──────────────────────────────────────
        if videos:
            # 下载所有视频缩略图
            for video_msg in videos:
                path = await download_thumbnail(client, app.temp_save_path, video_msg)
                thumb_paths.append(path)

            suffix = "\n\n视频详见评论区👇"
            if caption:
                main_caption = (
                    caption + suffix
                    if len(caption) + len(suffix) <= max_len
                    else caption[: max_len - len(suffix)] + suffix
                )
            else:
                main_caption = "视频详见评论区👇"

            # 主帖媒体列表：[第一张图片（若有）] + [各视频缩略图]
            main_media = []
            if photos:
                main_media.append(pyrogram.types.InputMediaPhoto(photos[0].photo.file_id))
            for path in thumb_paths:
                if path:
                    main_media.append(pyrogram.types.InputMediaPhoto(path))

            # 评论区：其余图片 + 所有视频
            comment_msgs = photos[1:] + videos

        else:
            # 纯图片：不改文案，不下缩略图
            main_caption = caption or None
            main_media = []   # 单张图片走 send_photo，不走 media_group
            comment_msgs = photos[1:]

        # ── 发主帖 ─────────────────────────────────────────────
        if videos:
            if not main_media:
                # 全部视频都没有缩略图，发文字占位
                dst_post = await upload_user.send_message(
                    node.upload_telegram_chat_id,
                    main_caption,
                    message_thread_id=node.topic_id,
                )
            elif len(main_media) == 1:
                dst_post = await upload_user.send_photo(
                    node.upload_telegram_chat_id,
                    main_media[0].media,
                    caption=main_caption,
                    message_thread_id=node.topic_id,
                )
            else:
                # 多张：第一项带文案，其余为空
                main_media[0] = pyrogram.types.InputMediaPhoto(
                    main_media[0].media, caption=main_caption
                )
                sent = await upload_user.send_media_group(
                    node.upload_telegram_chat_id,
                    main_media,
                    message_thread_id=node.topic_id,
                )
                dst_post = sent[0] if isinstance(sent, list) else sent
        else:
            # 纯图片
            dst_post = await upload_user.send_photo(
                node.upload_telegram_chat_id,
                photos[0].photo.file_id,
                caption=main_caption,
                message_thread_id=node.topic_id,
            )

        node.stat_forward(ForwardStatus.SuccessForward)

        # ── 发评论区 ────────────────────────────────────────────
        if comment_msgs:
            try:
                disc = await _get_discussion_message_retry(
                    client, node.upload_telegram_chat_id, dst_post.id
                )
                for msg in comment_msgs:
                    await app.forward_limit_call.wait(node)
                    try:
                        await msg.copy(disc.chat.id, reply_to_message_id=disc.id, caption="")
                    except Exception as e:
                        logger.warning(f"screenshot_split_group: copy {msg.id} to disc failed: {e}")
            except Exception:
                # 目标频道没有讨论组，直接发到频道
                for msg in comment_msgs:
                    try:
                        await msg.copy(node.upload_telegram_chat_id, caption="")
                    except Exception as e:
                        logger.warning(f"screenshot_split_group: fallback copy {msg.id} failed: {e}")

    except Exception as e:
        logger.warning(f"forward_screenshot_split_group failed: {e}")
        if dst_post is None:
            node.stat_forward(ForwardStatus.FailedForward)
    finally:
        for path in thumb_paths:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


async def _get_thread_replies(client, group_id, thread_msg_id):
    """Fetch all reply messages in a Telegram discussion thread via raw API."""
    from pyrogram.raw import functions as raw_fn
    try:
        peer = await client.resolve_peer(group_id)
        result = await client.invoke(
            raw_fn.messages.GetReplies(
                peer=peer,
                msg_id=thread_msg_id,
                offset_id=0,
                offset_date=0,
                add_offset=0,
                limit=100,
                max_id=0,
                min_id=0,
                hash=0,
            )
        )
        if not getattr(result, "messages", None):
            return []
        msg_ids = [m.id for m in result.messages]
        msgs = await client.get_messages(group_id, msg_ids)
        if not isinstance(msgs, list):
            msgs = [msgs]
        return [m for m in msgs if m and not getattr(m, "empty", False)]
    except Exception as e:
        logger.warning(f"_get_thread_replies({group_id}, {thread_msg_id}): {e}")
        return []


async def forward_clone_impl(client, app, node):
    """Clone channel: copy each post then copy its discussion-thread replies.

    Reproduces the source structure — e.g. photo-post in channel +
    video replies in the linked discussion group.
    """
    from module.get_chat_history_v2 import get_chat_history_v2

    seen_media_groups: set = set()

    async for post in get_chat_history_v2(
        client,
        node.chat_id,
        limit=node.limit,
        max_id=node.end_offset_id,
        offset_id=node.start_offset_id,
        reverse=True,
    ):
        if node.is_stop_transmission:
            break

        # For album/media-group posts only process the first message of each group
        if post.media_group_id:
            if post.media_group_id in seen_media_groups:
                continue
            seen_media_groups.add(post.media_group_id)

        await app.forward_limit_call.wait(node)

        # Refresh file reference before copying
        try:
            fresh = await client.get_messages(node.chat_id, post.id)
            if fresh and not getattr(fresh, "empty", False):
                post = fresh
        except Exception:
            pass

        # Copy the main post to the destination channel
        try:
            dst_post = await post.copy(
                node.upload_telegram_chat_id,
                message_thread_id=node.topic_id,
            )
            node.stat_forward(ForwardStatus.SuccessForward)
        except Exception as e:
            logger.warning(f"clone: copy post {post.id} failed: {e}")
            node.stat_forward(ForwardStatus.FailedForward)
            await report_bot_status(node.bot, node)
            continue

        # Clone the discussion thread for this post (if any)
        try:
            src_disc = await client.get_discussion_message(node.chat_id, post.id)
            dst_disc = await client.get_discussion_message(
                node.upload_telegram_chat_id, dst_post.id
            )
            reply_msgs = await _get_thread_replies(client, src_disc.chat.id, src_disc.id)
            for reply in reply_msgs:
                await app.forward_limit_call.wait(node)
                try:
                    fresh_reply = await client.get_messages(src_disc.chat.id, reply.id)
                    if fresh_reply and not getattr(fresh_reply, "empty", False):
                        reply = fresh_reply
                except Exception:
                    pass
                try:
                    await reply.copy(dst_disc.chat.id, reply_to_message_id=dst_disc.id)
                except Exception as e:
                    logger.warning(f"clone: copy discussion reply {reply.id} failed: {e}")
        except Exception:
            pass  # no discussion linked, or dst channel has no linked group

        await report_bot_status(node.bot, node)


def _needs_transcode(video_path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=10)
        return r.stdout.strip().lower() not in ("h264",)
    except Exception:
        return True

def _transcode_video(video_path):
    out = video_path + "_h264.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-c:v", "libx264",
             "-c:a", "aac", "-movflags", "+faststart", out, "-y"],
            capture_output=True, text=True, timeout=600)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
    except Exception:
        pass
    return None


async def _send_media_or_single(client, chat_id, media_list, **kwargs):
    if len(media_list) == 1:
        single = media_list[0]
        if isinstance(single, pyrogram.types.InputMediaVideo):
            await client.send_video(chat_id, single.media,
                caption=single.caption,
                width=getattr(single, 'width', None),
                height=getattr(single, 'height', None),
                duration=getattr(single, 'duration', None),
                supports_streaming=True, **kwargs)
        elif isinstance(single, pyrogram.types.InputMediaPhoto):
            await client.send_photo(chat_id, single.media,
                caption=single.caption, **kwargs)
    else:
        await client.send_media_group(chat_id, media_list, **kwargs)


async def _send_media_batched(client, chat_id, media_list, batch_size, **kwargs):
    """按 batch_size 分批发送（send_media_group 单批上限 10；分小批更稳，可规避
    大批一次性发送失败）。每批用相同 kwargs，都挂在同一锚点(如同一条 reply_to)下。"""
    for i in range(0, len(media_list), batch_size):
        await _send_media_or_single(client, chat_id, media_list[i:i + batch_size], **kwargs)


def _clean_caption(text: str) -> str:
    if not text:
        return text
    text = re.sub(r'\n?\s*火爆指数：[^\n]*\n?', '', text).strip()
    text = re.sub(r'\n?\s*剩下\d+V点\s*https?://\S+', '', text).strip()
    text = re.sub(r'\s*📱{3,}[\s\S]*$', '', text).strip()
    return text


def _code_file_uid(msg):
    """取消息媒体的 file_unique_id（用于去重）"""
    media = (getattr(msg, "photo", None) or getattr(msg, "video", None)
             or getattr(msg, "document", None))
    return getattr(media, "file_unique_id", None) if media else None


async def forward_code_impl(
    client: pyrogram.Client,
    app: Application,
    node: TaskNode,
    bot_chat_id: int,
    code: str,
) -> list:
    """通过提取码从 Bot 获取资源并转发到目标频道"""
    sent = await client.send_message(bot_chat_id, f"/start {code}")
    logger.info(f"[forward_code] sent /start {code} to {bot_chat_id}, start_msg_id={sent.id}")

    resources = await _poll_code_replies(client, bot_chat_id, sent.id, timeout=30)

    logger.info(f"[forward_code] collected {len(resources)} resource messages for code {code}")

    if not resources:
        node.stat_forward(ForwardStatus.FailedForward)
        await report_bot_status(node.bot, node, immediate_reply=True)
        return []

    # 统计与进度刷新已下沉到 _upload_code_resources，逐条实时更新
    failed_ids = await _upload_code_resources(client, node, resources)

    return failed_ids


async def _poll_code_replies(
    client: pyrogram.Client,
    bot_chat_id: int,
    start_msg_id: int,
    timeout: int = 30,
) -> list:
    """轮询 bot 私聊，收集回复中的媒体消息，处理翻页"""
    poll_interval = 2
    start_time = time.time()
    last_seen_id = start_msg_id
    resources = []

    while time.time() - start_time < timeout:
        await asyncio.sleep(poll_interval)

        try:
            msgs = [m async for m in client.get_chat_history(bot_chat_id, limit=50)]
        except Exception as e:
            logger.warning(f"[forward_code] poll error: {e}")
            continue

        all_done = False
        # get_chat_history 返回「新→旧」，必须反转成「旧→新」处理，
        # 否则 last_seen_id 在循环内被顶到本轮最大 id，会把同一批里更旧的消息全部跳过（丢资源）
        for msg in reversed(msgs):
            if msg.id <= last_seen_id:
                continue
            if not msg.from_user or msg.from_user.id != bot_chat_id:
                continue
            last_seen_id = msg.id

            text = (msg.text or msg.caption or "").strip()

            # 诊断：打印 bot 每条回复的结构，便于判断资源是「一次发完 / 翻页 / 按钮列表」哪种形态
            btn_texts = [
                btn.text
                for row in (msg.reply_markup.inline_keyboard if msg.reply_markup else [])
                for btn in row
            ]
            logger.info(
                f"[forward_code] poll msg {msg.id}: media={bool(msg.media)} "
                f"group={msg.media_group_id} buttons={btn_texts} text={text[:40]!r}"
            )

            if "已全部获取" in text:
                all_done = True
                continue

            if "检测到共" in text and "个资源" in text:
                continue

            if msg.media:
                resources.append(msg)

            if msg.reply_markup and not all_done:
                for row in msg.reply_markup.inline_keyboard:
                    for btn in row:
                        if "下一页" in btn.text:
                            try:
                                await client.request_callback_answer(
                                    bot_chat_id, msg.id, btn.callback_data
                                )
                                start_time = time.time()
                                logger.info(f"[forward_code] clicked 下一页 on msg {msg.id}")
                            except Exception as e:
                                logger.warning(f"[forward_code] click 下一页 failed: {e}")
                            break

        if all_done:
            return resources

    logger.info(f"[forward_code] poll timeout reached")
    return resources


async def _upload_code_resources(
    client: pyrogram.Client,
    node: TaskNode,
    resources: list,
) -> list:
    """转发资源消息到目标频道：优先服务端 copy（不下载），失败再回退到下载+重新上传"""
    upload_user = node.upload_user or client
    target_chat = node.upload_telegram_chat_id
    temp_files = []
    failed_ids = []
    uploaded_ids = node.forward_code_uploaded_ids

    groups = {}
    singles = []
    for msg in resources:
        if msg.media_group_id:
            groups.setdefault(msg.media_group_id, []).append(msg)
        else:
            singles.append(msg)

    async def _mark(msgs, success: bool):
        """逐条更新转发统计（复用 forward 的进度反馈）：失败的同时记入 failed_ids，并刷新进度消息"""
        node.forward_code_progress_str = ""   # 处理完一组/一条，清掉临时进度行
        if not isinstance(msgs, (list, tuple)):
            msgs = [msgs]
        for m in msgs:
            if success:
                node.stat_forward(ForwardStatus.SuccessForward)
            else:
                node.stat_forward(ForwardStatus.FailedForward)
                if m.id not in failed_ids:
                    failed_ids.append(m.id)
        await report_bot_status(node.bot, node)

    async def _progress(current, total, prefix):
        """download/upload 进度回调：更新进度字段并（节流）刷新 taskid 消息"""
        pct = int(current / max(total, 1) * 100)
        icon = "📤" if "上传" in prefix else "📥"
        node.forward_code_progress_str = (
            f"\n{icon} {prefix} [{create_progress_bar(pct)}] {pct}%\n"
        )
        await report_bot_status(node.bot, node)

    def _remember(msgs):
        """记录已转发的 file_unique_id，供翻页去重"""
        for m in (msgs if isinstance(msgs, (list, tuple)) else [msgs]):
            uid = _code_file_uid(m)
            if uid:
                uploaded_ids.add(uid)

    def _all_duplicated(msgs):
        """整组/单条的媒体是否都已转发过（翻页重复）"""
        msgs = msgs if isinstance(msgs, (list, tuple)) else [msgs]
        uids = [_code_file_uid(m) for m in msgs]
        return all(u and u in uploaded_ids for u in uids)

    async def _upload_single(msg, file_path, file_unique_id):
        if file_unique_id and file_unique_id in uploaded_ids:
            logger.info(f"[forward_code] skip duplicate: {file_unique_id}")
            return True

        caption = _clean_caption(msg.caption or "")
        try:
            if msg.photo:
                await upload_user.send_photo(
                    target_chat, file_path, caption=caption,
                    progress=_progress, progress_args=("上传中",),
                )
            elif msg.video:
                await upload_user.send_video(
                    target_chat, file_path, caption=caption,
                    width=msg.video.width, height=msg.video.height,
                    duration=msg.video.duration, supports_streaming=True,
                    progress=_progress, progress_args=("上传中",),
                )
            elif msg.document:
                await upload_user.send_document(
                    target_chat, file_path, caption=caption,
                    file_name=getattr(msg.document, "file_name", None),
                    progress=_progress, progress_args=("上传中",),
                )
            elif msg.audio:
                await upload_user.send_audio(
                    target_chat, file_path, caption=caption,
                    progress=_progress, progress_args=("上传中",),
                )
            elif msg.voice:
                await upload_user.send_voice(
                    target_chat, file_path, caption=caption,
                    progress=_progress, progress_args=("上传中",),
                )
            elif msg.animation:
                await upload_user.send_animation(
                    target_chat, file_path, caption=caption,
                    progress=_progress, progress_args=("上传中",),
                )
            elif msg.video_note:
                await upload_user.send_video_note(
                    target_chat, file_path,
                    progress=_progress, progress_args=("上传中",),
                )
            else:
                await upload_user.send_document(
                    target_chat, file_path, caption=caption,
                    progress=_progress, progress_args=("上传中",),
                )

            if file_unique_id:
                uploaded_ids.add(file_unique_id)
            return True
        except Exception as e:
            logger.warning(f"[forward_code] upload msg {msg.id} failed: {e}")
            return False

    async def _fallback_group(group_id, group_msgs):
        """copy 失败时的兜底：逐条下载到本地后用 send_media_group 重新上传"""
        group_temp = []
        group_ok = True
        n = len(group_msgs)

        for idx, msg in enumerate(group_msgs, 1):
            try:
                file_path = await client.download_media(
                    msg, progress=_progress, progress_args=(f"下载中 {idx}/{n}",)
                )
                if not file_path:
                    raise Exception("download_media returned None")
                temp_files.append(file_path)
                group_temp.append(file_path)
            except Exception as e:
                logger.warning(f"[forward_code] download msg {msg.id} failed: {e}")
                group_ok = False

        if not group_ok:
            # 整组任一文件下载失败则整组放弃，全组都计入失败，避免静默漏发+误报成功
            await _mark(group_msgs, False)
            return

        media_list = []
        for i, msg in enumerate(group_msgs):
            file_path = group_temp[i]
            caption = _clean_caption(msg.caption or "")
            if msg.photo:
                media_list.append(types.InputMediaPhoto(file_path, caption=caption))
            elif msg.video:
                media_list.append(types.InputMediaVideo(
                    file_path, caption=caption,
                    width=msg.video.width, height=msg.video.height,
                    duration=msg.video.duration, supports_streaming=True,
                ))
            else:
                media_list.append(types.InputMediaDocument(file_path, caption=caption))

        if not media_list:
            return

        if len(media_list) == 1:
            # 媒体组里只剩 1 个有效媒体：走单条上传
            ok = await _upload_single(
                group_msgs[0], group_temp[0], _code_file_uid(group_msgs[0])
            )
            await _mark(group_msgs[0], ok)
            return

        try:
            node.forward_code_progress_str = f"\n📤 上传中 {len(media_list)} 个文件...\n"
            await report_bot_status(node.bot, node)
            await upload_user.send_media_group(target_chat, media_list)
            _remember(group_msgs)
            await _mark(group_msgs, True)
        except Exception as e:
            logger.warning(f"[forward_code] send_media_group failed for group {group_id}: {e}, falling back")
            for i, msg in enumerate(group_msgs):
                ok = await _upload_single(msg, group_temp[i], _code_file_uid(msg))
                await _mark(msg, ok)

    async def _fallback_single(msg):
        """copy 失败时的兜底：下载到本地后重新上传"""
        try:
            file_path = await client.download_media(
                msg, progress=_progress, progress_args=("下载中",)
            )
            if not file_path:
                raise Exception("download_media returned None")
            temp_files.append(file_path)
        except Exception as e:
            logger.warning(f"[forward_code] download msg {msg.id} failed: {e}")
            await _mark(msg, False)
            return

        ok = await _upload_single(msg, file_path, _code_file_uid(msg))
        await _mark(msg, ok)

    # ---- 媒体组：优先 copy_media_group（服务端复制，不下载不上传）----
    for group_id, group_msgs in groups.items():
        if node.is_stop_transmission:
            break
        if _all_duplicated(group_msgs):
            logger.info(f"[forward_code] skip duplicate group {group_id}")
            continue

        captions = [_clean_caption(m.caption or "") for m in group_msgs]
        try:
            await client.copy_media_group(
                target_chat, group_msgs[0].chat.id, group_msgs[0].id, captions=captions
            )
            _remember(group_msgs)
            await _mark(group_msgs, True)
        except Exception as e:
            logger.warning(
                f"[forward_code] copy_media_group failed for group {group_id}: {e}, "
                "fallback to download+upload"
            )
            await _fallback_group(group_id, group_msgs)

    # ---- 单条：优先 copy_message（服务端复制，不下载不上传）----
    for msg in singles:
        if node.is_stop_transmission:
            break
        if _all_duplicated(msg):
            logger.info(f"[forward_code] skip duplicate single {msg.id}")
            continue

        try:
            await client.copy_message(
                target_chat, msg.chat.id, msg.id,
                caption=_clean_caption(msg.caption or ""),
            )
            _remember(msg)
            await _mark(msg, True)
        except Exception as e:
            logger.warning(
                f"[forward_code] copy_message failed for msg {msg.id}: {e}, "
                "fallback to download+upload"
            )
            await _fallback_single(msg)

    for f in temp_files:
        try:
            if os.path.isfile(f):
                os.remove(f)
        except Exception:
            pass

    return failed_ids
