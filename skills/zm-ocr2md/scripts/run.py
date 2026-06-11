#!/usr/bin/env python3
"""
zm-ocr2md: 将单张本地图像 OCR 提取文字并保存为 Markdown
支持三种 OCR 模式：
  - mcp（默认）：MCP 视觉模型 understand_image，优先 minimax-coding-plan-mcp，失败时降级到 moonshot-vision
  - api：远程 API（OpenAI / Gemini / Anthropic / Azure 等）
  - local：本地 OCR（PaddleOCR / Chandra OCR 2）
仅支持单张图像文件（.png/.jpg/.jpeg/.webp）。
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import NamedTuple

# 默认配置
DEFAULT_CONFIG = {
    "provider": "mcp",  # mcp | api | local
    # 远程 API 配置
    "API_BASE": "https://api.openai.com/v1",
    "API_KEY": "",
    "API_MODEL": "gpt-4o",
}

SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
# 后缀到 MIME 类型的映射；与 SUPPORTED_IMAGE_SUFFIXES 严格对齐
# 新增格式时同时更新这两个集合；get_mime_type 复用，避免 DRY 违反
_SUFFIX_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
DEFAULT_LOCAL_CONFIG = {
    # 公共参数
    "LOCAL_CACHE_DIR": "",
    # 引擎选择
    "LOCAL_ENGINE": "paddle",   # paddle | chandra
    # PaddleOCR 参数
    "PADDLE_PARAMS": "--use_textline_orientation=True --lang=ch",  # 注意：3.4.1 已移除 show_log、use_gpu
    # Chandra OCR 2 参数
    "CHANDRA_BACKEND": "hf",   # hf | vllm | docker
    "CHANDRA_VLLM_ENDPOINT": "http://localhost:8000/v1",
    "CHANDRA_DOCKER_ENDPOINT": "http://localhost:8501/v1",
}

CONFIG_PATH = Path.home() / ".config" / "zm-ocr2md" / ".env"
LOCAL_CONFIG_PATH = Path.home() / ".config" / "zm-ocr2md" / ".local_env"


class Result(NamedTuple):
    status: str
    message: str
    files_created: list[str]
    details: dict


def safe_filename(name: str) -> str:
    """将字符串转换为安全的文件系统名字，去除控制字符和文件系统非法字符，空格转为下划线。"""
    invalid_chars = '<>:"/\\|?*'
    # Unicode 双向控制字符（防止文件名展示混淆）
    # 注意：'L'/'R'/'AL' 是默认字符方向类，'L'/'R' 几乎覆盖所有拉丁字母，不能过滤
    # 完整覆盖 embedding (LRE/RLE/PDF/LRO/RLO) + isolate (LRI/RLI/FSI/PDI) 全部双向控制字符
    bidi_chars = {
        "RLE", "LRE", "RLO", "LRO", "PDF",
        "LRI", "RLI", "FSI", "PDI",
    }
    cleaned = "".join(
        c for c in name
        if (unicodedata.category(c)[0] != "C" or c in ("_", "-"))
        and unicodedata.bidirectional(c) not in bidi_chars
        and c not in invalid_chars
    ).rstrip(" .").replace(" ", "_")
    cleaned = re.sub(r"_+", "_", cleaned)
    if cleaned:
        return cleaned
    # fallback：避免多张图都退化为同名 "unnamed.md" 互相覆盖
    # 使用 blake2b 而非 md5，非密码学场景更现代
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=3).hexdigest()[:6]
    return f"unnamed_{digest}"


def load_env_config(env_file: str | None) -> dict:
    """加载 .env 配置文件（远程 API 用）。"""
    if env_file:
        p = Path(env_file)
    else:
        p = CONFIG_PATH

    if p.exists():
        try:
            env_vars = {}
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, value = line.split("=", 1)
                        env_vars[key.strip()] = value.strip().strip('"').strip("'")
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(env_vars)
            return cfg
        except Exception as e:
            print(f"警告: 配置文件读取失败 ({e})，使用默认配置", file=sys.stderr)
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()


def load_local_config(local_env_file: str | None) -> dict:
    """加载 .local_env 配置文件（本地 OCR 用）。"""
    if local_env_file:
        p = Path(local_env_file)
    else:
        p = LOCAL_CONFIG_PATH

    if p.exists():
        try:
            env_vars = {}
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, value = line.split("=", 1)
                        env_vars[key.strip()] = value.strip().strip('"').strip("'")
            cfg = DEFAULT_LOCAL_CONFIG.copy()
            cfg.update(env_vars)
            return cfg
        except Exception as e:
            print(f"警告: 本地 OCR 配置读取失败 ({e})，使用默认配置", file=sys.stderr)
            return DEFAULT_LOCAL_CONFIG.copy()
    return DEFAULT_LOCAL_CONFIG.copy()


def parse_paddle_params(params_str: str) -> dict:
    """解析 PaddleOCR 参数字符串为字典。"""
    params = {}
    # 匹配 --key=value 或 --flag 格式
    pattern = r'--(\w+)(?:=(.+?))?(?=\s+--|\s*$|$)'
    matches = re.findall(pattern, params_str)
    for key, value in matches:
        if value is None:
            value = "True"
        # 处理 Python 布尔值
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        elif value.lower() == "none":
            value = None
        params[key] = value
    return params


def get_mime_type(suffix: str) -> str | None:
    """根据文件后缀返回 MIME 类型。

    仅识别 SUPPORTED_IMAGE_SUFFIXES 中已声明的格式；找不到返回 None。
    ocr_with_api 与 resolve_single_image 共用，避免后缀处理逻辑分散。
    """
    return _SUFFIX_TO_MIME.get(suffix.lower())


def resolve_single_image(path: str) -> tuple[Path | None, str, str | None]:
    """解析单张图像输入，返回 (image, status, message)。

    zm-ocr2md 1.0.0 起只接受单张本地图像文件（.png/.jpg/.jpeg/.webp）。
    目录与清单类输入请改用 zm-batch-ocr2md。
    """
    p = Path(path)

    if not p.exists():
        return None, "missing_input", f"输入路径不存在: {path}"

    if p.is_dir():
        return None, "non_single_input", "zm-ocr2md 仅接受单张本地图像；目录或清单请使用 zm-batch-ocr2md"

    if not p.is_file():
        return None, "non_single_input", "zm-ocr2md 仅接受单张本地图像文件"

    suffix = p.suffix.lower()
    if suffix in SUPPORTED_IMAGE_SUFFIXES:
        return p, "ok", None

    return None, "unsupported_format", f"不支持的图像格式: {suffix or '(无扩展名)'}"


def ocr_with_mcp(image_path: Path) -> Result:
    """使用 MCP 视觉模型的 understand_image 工具：优先 minimax-coding-plan-mcp，失败时降级到 moonshot-vision。"""
    return Result(
        status="mcp_required",
        message="请使用 MCP 视觉模型 understand_image：优先 minimax-coding-plan-mcp，失败或不可用时降级到 moonshot-vision",
        files_created=[],
        details={"image": str(image_path), "provider": "mcp"},
    )


def ocr_with_api(image_path: Path, config: dict, require_api_key: bool = True) -> Result:
    """使用远程 OpenAI 兼容 API 进行 OCR。"""
    import urllib.request
    import urllib.error

    api_base = config.get("API_BASE", "https://api.openai.com/v1")
    api_key = config.get("API_KEY", "")
    model = config.get("API_MODEL", "gpt-4o")

    if require_api_key and not api_key:
        return Result(
            status="openai_api_failed",
            message="API_KEY 未配置，请检查 ~/.config/zm-ocr2md/.env",
            files_created=[],
            details={"image": str(image_path)},
        )

    # 读取图像并转为 base64
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
        image_base64 = base64.b64encode(image_data).decode("utf-8")
    except Exception as e:
        return Result(
            status="openai_api_failed",
            message=f"读取图像失败: {e}",
            files_created=[],
            details={"image": str(image_path)},
        )

    # 根据文件扩展名确定 mime 类型（复用 get_mime_type，与 SUPPORTED_IMAGE_SUFFIXES 严格对齐）
    mime_type = get_mime_type(image_path.suffix)
    if mime_type is None:
        return Result(
            status="unsupported_format",
            message=f"不支持的图像格式: {image_path.suffix or '(无扩展名)'}",
            files_created=[],
            details={"image": str(image_path)},
        )

    # max_tokens 配置读取（带空值保护）
    max_tokens_raw = config.get("MAX_TOKENS", 8192)
    try:
        max_tokens = int(max_tokens_raw) if max_tokens_raw else 8192
    except (ValueError, TypeError):
        max_tokens = 8192

    # API 请求超时配置（带空值保护，默认 120 秒）
    api_timeout_raw = config.get("API_TIMEOUT", 120)
    try:
        api_timeout = int(api_timeout_raw) if api_timeout_raw else 120
    except (ValueError, TypeError):
        api_timeout = 120

    # OCR 提示词配置（默认保持原有行为）
    ocr_prompt = config.get(
        "OCR_PROMPT",
        "请提取这张图片中的所有文字，以 Markdown 格式输出。只输出文字内容，不要其他解释。",
    )

    # 构建请求
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
    }
    # vLLM/Docker 本地后端可能不需要 API_KEY；空 key 时不发送 Authorization 头
    # 避免某些 OpenAI 兼容服务对 "Bearer " 空值返回 401
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": ocr_prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_base64}",
                        },
                    },
                ],
            }
        ],
        "max_tokens": max_tokens,
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=api_timeout) as response:
            result_data = json.loads(response.read().decode("utf-8"))

        if "choices" in result_data and len(result_data["choices"]) > 0:
            text = result_data["choices"][0]["message"]["content"]
            return Result(
                status="success",
                message="远程 API OCR 成功",
                files_created=[],
                details={"image": str(image_path), "text": text},
            )
        else:
            return Result(
                status="openai_api_failed",
                message=f"API 响应格式异常: {result_data}",
                files_created=[],
                details={"image": str(image_path)},
            )

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        return Result(
            status="openai_api_failed",
            message=f"远程 API HTTP 错误 {e.code}: {error_body[:200]}",
            files_created=[],
            details={"image": str(image_path)},
        )
    except Exception as e:
        return Result(
            status="openai_api_failed",
            message=f"远程 API 调用失败: {e}",
            files_created=[],
            details={"image": str(image_path)},
        )


def ocr_with_paddle(image_path: Path, params: dict, local_config: dict | None = None) -> Result:
    """使用 PaddleOCR 进行 OCR（通过 conda run -n ocr 调用）。

    策略：
    - 通过 subprocess + conda run 在 ocr conda 环境中执行
    - 检查 PaddleOCR 模型缓存是否存在
      - 缓存存在 → 设置 PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True，强制使用本地缓存
      - 缓存不存在 → 允许联网下载模型
    """
    import json as _json
    import tempfile as _tempfile

    # 解析参数
    use_textline_orientation = params.get("use_textline_orientation", True)
    lang = params.get("lang", "ch")
    max_size_raw = params.get("max_image_size", (local_config or {}).get("MAX_IMAGE_SIZE", 1000))
    try:
        max_size = int(max_size_raw)
    except (ValueError, TypeError):
        max_size = 1000

    # 准备环境变量（清理 socks 代理）
    env = os.environ.copy()
    for k in list(env.keys()):
        k_lower = k.lower()
        if k_lower == "all_proxy" or k_lower == "socks_proxy":
            del env[k]
        elif "proxy" in k_lower and "socks" in k_lower:
            del env[k]

    # 检查模型缓存
    paddle_cache = Path.home() / ".paddlex" / "official_models"
    if paddle_cache.exists() and any(paddle_cache.iterdir()):
        env["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

    # 创建临时脚本和临时文件
    fd, tmp_script_path = _tempfile.mkstemp(suffix="_paddle_ocr.py", prefix="zm-ocr2md_")
    os.close(fd)
    tmp_script = Path(tmp_script_path)
    tmp_img = None

    # 预处理大图像（先检查尺寸）
    try:
        from PIL import Image as _Image
        img = _Image.open(image_path)
        width, height = img.size
        if max(width, height) > max_size:
            ratio = max_size / max(width, height)
            new_width = int(width * ratio)
            new_height = int(height * ratio)
            try:
                resample = _Image.Resampling.LANCZOS
            except AttributeError:
                resample = _Image.LANCZOS  # type: ignore[reportAttributeAccessIssue]
            img = img.resize((new_width, new_height), resample)
            fd_img, tmp_img_path = _tempfile.mkstemp(suffix=image_path.suffix, prefix="zm-ocr2md_paddle_img_")
            os.close(fd_img)
            tmp_img = Path(tmp_img_path)
            img.save(tmp_img)
            img_path_for_ocr = str(tmp_img)
        else:
            img_path_for_ocr = str(image_path)
    except Exception as e:
        # 预处理失败，清理已创建的临时脚本
        if tmp_script.exists():
            tmp_script.unlink(missing_ok=True)
        return Result(
            status="paddleocr_failed",
            message=f"PaddleOCR 图像预处理失败: {e}",
            files_created=[],
            details={"image": str(image_path)},
        )

    # 构建脚本内容（所有参数都通过 repr() / json.dumps 安全转义为 Python 字面量，避免 f-string 注入风险）
    script_content = f'''
import sys
import json
from pathlib import Path

try:
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(use_textline_orientation={repr(use_textline_orientation)}, lang={json.dumps(lang)})
    result = ocr.ocr({json.dumps(img_path_for_ocr)})

    if not result or not result[0]:
        print(json.dumps({{"status": "empty", "text": ""}}))
        sys.exit(0)

    lines = []
    first_result = result[0] if isinstance(result[0], dict) else result[0]

    if isinstance(first_result, dict) and "rec_texts" in first_result:
        for item in result:
            if item.get("rec_texts"):
                lines.extend(item["rec_texts"])
    else:
        for line in result[0]:
            if line:
                text = line[1][0] if isinstance(line[1], tuple) else line[1]
                lines.append(text)

    print(json.dumps({{"status": "success", "text": "\\n".join(lines)}}))
except ImportError as e:
    print(json.dumps({{"status": "import_error", "error": str(e)}}))
    sys.exit(1)
except Exception as e:
    print(json.dumps({{"status": "error", "error": str(e)}}))
    sys.exit(2)
'''

    try:
        tmp_script.write_text(script_content, encoding="utf-8")

        result = subprocess.run(
            ["conda", "run", "-n", "ocr", "python", str(tmp_script)],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )

        # 解析输出
        if result.returncode == 0 and result.stdout.strip():
            try:
                ocr_data = _json.loads(result.stdout.strip())
                if ocr_data.get("status") == "success":
                    return Result(
                        status="success",
                        message="PaddleOCR 成功",
                        files_created=[],
                        details={"image": str(image_path), "text": ocr_data.get("text", "")},
                    )
                elif ocr_data.get("status") == "empty":
                    return Result(
                        status="paddleocr_failed",
                        message="PaddleOCR 未检测到文字",
                        files_created=[],
                        details={"image": str(image_path)},
                    )
                else:
                    return Result(
                        status="paddleocr_failed",
                        message=f"PaddleOCR 推理失败: {ocr_data.get('error', 'unknown')}",
                        files_created=[],
                        details={"image": str(image_path)},
                    )
            except _json.JSONDecodeError:
                return Result(
                    status="paddleocr_failed",
                    message=f"PaddleOCR 输出解析失败: {result.stdout[:200]}",
                    files_created=[],
                    details={"image": str(image_path)},
                )
        elif result.returncode == 1:
            return Result(
                status="paddleocr_import_failed",
                message="PaddleOCR 导入失败，请检查 conda 环境 ocr",
                files_created=[],
                details={"image": str(image_path)},
            )
        else:
            error_msg = result.stderr[:500] if result.stderr else "unknown"
            return Result(
                status="paddleocr_failed",
                message=f"PaddleOCR 推理失败: {error_msg}",
                files_created=[],
                details={"image": str(image_path)},
            )

    except subprocess.TimeoutExpired:
        return Result(
            status="paddleocr_failed",
            message="PaddleOCR 推理超时（>300s）",
            files_created=[],
            details={"image": str(image_path)},
        )
    except FileNotFoundError:
        return Result(
            status="paddleocr_import_failed",
            message="conda 命令未找到，请确认已安装 conda",
            files_created=[],
            details={"image": str(image_path)},
        )
    except Exception as e:
        return Result(
            status="paddleocr_failed",
            message=f"PaddleOCR 调用异常: {e}",
            files_created=[],
            details={"image": str(image_path)},
        )
    finally:
        # 清理临时图像文件
        if tmp_img and tmp_img.exists():
            tmp_img.unlink(missing_ok=True)
        # 清理临时脚本
        if tmp_script.exists():
            tmp_script.unlink(missing_ok=True)


def ocr_with_chandra(
    image_path: Path,
    output_dir: Path,
    method: str = "hf",
    local_config: dict | None = None,
) -> Result:
    """使用 conda run -n ocr python -m chandra.scripts.cli --method hf|vllm 进行 OCR。

    策略：
    - HF 后端：优先检查模型缓存是否存在
      - 缓存存在 → 设置 HF_HUB_OFFLINE=1，强制使用本地缓存
      - 缓存不存在 → 允许联网下载模型
    - vLLM/Docker 后端：不走本地模型，无需此逻辑
    """
    # 准备环境变量
    env = os.environ.copy()

    # 超时配置（带空值保护，默认 600 秒）
    chandra_timeout_raw = (local_config or {}).get("CHANDRA_TIMEOUT", 600)
    try:
        chandra_timeout = int(chandra_timeout_raw) if chandra_timeout_raw else 600
    except (ValueError, TypeError):
        chandra_timeout = 600

    # 清理会导致 httpx 失败的 socks 代理变量（保留 http/https 代理）
    for k in list(env.keys()):
        k_lower = k.lower()
        if k_lower == "all_proxy" or k_lower == "socks_proxy":
            # 完全删除 socks 代理变量
            del env[k]
        elif "proxy" in k_lower and "socks" in k_lower:
            # 删除包含 socks 的其他代理变量
            del env[k]

    # HF 后端：检查模型缓存是否已存在
    if method == "hf":
        cache_path = Path.home() / ".cache" / "huggingface" / "hub"
        # 检查 Chandra 模型的默认缓存路径是否存在
        model_cached = (cache_path / "models--ArliAI--chandra-ocr-2").exists()

        if model_cached:
            # 模型已缓存，强制离线模式
            env["HF_HUB_OFFLINE"] = "1"
            env["TRANSFORMERS_OFFLINE"] = "1"
        else:
            # 模型未缓存，清除离线模式变量，允许联网下载
            env.pop("HF_HUB_OFFLINE", None)
            env.pop("TRANSFORMERS_OFFLINE", None)

    try:
        result = subprocess.run(
            [
                "conda", "run", "-n", "ocr",
                "python", "-m", "chandra.scripts.cli",
                "--method", method,
                str(image_path),
                str(output_dir),
            ],
            capture_output=True,
            text=True,
            timeout=chandra_timeout,
            env=env,
        )

        if result.returncode == 0:
            # Chandra OCR 2 成功，查找输出的 md 文件
            stem = safe_filename(image_path.stem)
            out_md = output_dir / f"{stem}.md"
            text = ""
            if out_md.exists():
                try:
                    text = out_md.read_text(encoding="utf-8")
                except Exception as e:
                    return Result(
                        status="save_failed",
                        message=f"读取 Chandra OCR 输出失败: {e}",
                        files_created=[],
                        details={"image": str(image_path), "method": method},
                    )
            return Result(
                status="success",
                message=f"Chandra OCR 2 ({method}) 成功",
                files_created=[str(out_md)] if out_md.exists() else [],
                details={"image": str(image_path), "text": text, "method": method},
            )
        else:
            error_msg = result.stderr[:500] if result.stderr else "unknown"
            # 提供更友好的错误提示
            if "offline" in error_msg.lower() or "cache" in error_msg.lower():
                hint = "模型缓存不存在，正在尝试联网下载..."
            elif "connection" in error_msg.lower() or "network" in error_msg.lower():
                hint = "网络连接问题，请检查代理设置"
            else:
                hint = ""
            full_msg = f"Chandra OCR 2 ({method}) 执行失败: {error_msg}" + (f" {hint}" if hint else "")
            return Result(
                status="chandra_inference_failed",
                message=full_msg,
                files_created=[],
                details={"image": str(image_path), "method": method},
            )

    except subprocess.TimeoutExpired:
        return Result(
            status="chandra_inference_failed",
            message=f"Chandra OCR 2 推理超时（>{chandra_timeout}s）",
            files_created=[],
            details={"image": str(image_path)},
        )
    except FileNotFoundError:
        return Result(
            status="chandra_import_failed",
            message="conda 命令未找到，请确认已安装 conda",
            files_created=[],
            details={"image": str(image_path)},
        )
    except Exception as e:
        return Result(
            status="chandra_inference_failed",
            message=f"Chandra OCR 2 调用异常: {e}",
            files_created=[],
            details={"image": str(image_path)},
        )


def process_image(
    image_path: Path,
    output_dir: Path,
    provider: str,
    config: dict,
    local_config: dict,
) -> Result:
    """处理单张图像：OCR → 保存 Markdown。"""
    ocr_result = Result(status="unknown", message="", files_created=[], details={})
    stem = safe_filename(image_path.stem)

    # 确定输出路径
    out_md = output_dir / f"{stem}.md"

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return Result(
            status="output_dir_create_failed",
            message=f"无法创建输出目录 {output_dir}: {e}",
            files_created=[],
            details={"image": str(image_path)},
        )

    # OCR
    if provider == "mcp":
        ocr_result = ocr_with_mcp(image_path)
    elif provider == "api":
        ocr_result = ocr_with_api(image_path, config)
    elif provider == "local":
        engine = local_config.get("LOCAL_ENGINE", "paddle")

        if engine == "paddle":
            # PaddleOCR
            params_str = local_config.get("PADDLE_PARAMS", "")
            paddle_params = parse_paddle_params(params_str)
            # 允许 CLI 参数覆盖
            if "use_gpu" in local_config:
                paddle_params["use_gpu"] = local_config["use_gpu"]
            ocr_result = ocr_with_paddle(image_path, paddle_params, local_config)

        elif engine == "chandra":
            backend = local_config.get("CHANDRA_BACKEND", "hf")

            if backend == "hf":
                # HF 后端（通过 conda run 调用）
                import tempfile as _tf
                temp_out = Path(_tf.mkdtemp(prefix="zm-ocr2md_chandra_"))
                try:
                    chandra_result = ocr_with_chandra(image_path, temp_out, method="hf", local_config=local_config)

                    # 移动结果到标准位置
                    if chandra_result.status == "success":
                        md_files = list(temp_out.glob("*.md"))
                        if md_files:
                            try:
                                content = md_files[0].read_text(encoding="utf-8")
                                out_md.write_text(content, encoding="utf-8")
                                chandra_result = Result(
                                    status="success",
                                    message=chandra_result.message,
                                    files_created=[str(out_md)],
                                    details=chandra_result.details,
                                )
                            except Exception as e:
                                chandra_result = Result(
                                    status="save_failed",
                                    message=f"保存 Markdown 失败: {e}",
                                    files_created=[],
                                    details={"image": str(image_path)},
                                )
                    ocr_result = chandra_result
                finally:
                    # 清理临时目录（无论 chandra 成功/失败均清理）
                    try:
                        import shutil as _shutil
                        _shutil.rmtree(temp_out)
                    except Exception:
                        pass

            elif backend == "vllm":
                # vLLM 后端（通过 API 调用）
                vllm_endpoint = local_config.get("CHANDRA_VLLM_ENDPOINT", "http://localhost:8000/v1")
                local_api_config = {
                    "API_BASE": vllm_endpoint,
                    "API_KEY": "",
                    "API_MODEL": "chandra-ocr-2",
                }
                ocr_result = ocr_with_api(image_path, local_api_config, require_api_key=False)

            elif backend == "docker":
                # Docker 后端（通过 API 调用）
                docker_endpoint = local_config.get("CHANDRA_DOCKER_ENDPOINT", "http://localhost:8501/v1")
                local_api_config = {
                    "API_BASE": docker_endpoint,
                    "API_KEY": "",
                    "API_MODEL": "chandra-ocr-2",
                }
                ocr_result = ocr_with_api(image_path, local_api_config, require_api_key=False)

            else:
                return Result(
                    status="chandra_inference_failed",
                    message=f"未知的 Chandra 后端: {backend}",
                    files_created=[],
                    details={"image": str(image_path)},
                )

        else:
            return Result(
                status="paddleocr_failed",
                message=f"未知的本地 OCR 引擎: {engine}",
                files_created=[],
                details={"image": str(image_path)},
            )

    if ocr_result.status != "success":
        return ocr_result

    text = ocr_result.details.get("text", "")

    # 保存 Markdown
    try:
        with open(out_md, "w", encoding="utf-8") as f:
            f.write(f"# {stem}\n\n")
            f.write(text)
            f.write("\n")
        return Result(
            status="success",
            message=f"OCR 成功，生成了 {out_md}",
            files_created=[str(out_md)],
            details={"image": str(image_path), "text": text},
        )
    except Exception as e:
        return Result(
            status="save_failed",
            message=f"保存 Markdown 失败: {e}",
            files_created=[],
            details={"image": str(image_path)},
        )


def run(
    path: str,
    env_file: str | None,
    local_env_file: str | None,
    provider: str,
    output_dir: str | None,
) -> dict:
    """核心运行函数。

    返回结构（顶层）：
      - result_status: 聚合状态（success / partial / 输入与目录类状态码）
      - summary: 人类可读汇总文案
      - targets: 单图合同的 target 列表（仅 1 个元素）
      - files_created: 全部成功写出的 Markdown 路径

    target 字典字段：
      - source: 输入图像绝对路径
      - status: 实际状态码（success / mcp_required / 各 provider 失败码）
      - message: 状态描述
      - output_files: 该 target 写出的文件列表
      - level: per_item（子项视角）/ aggregate（汇总视角），便于区分
    """
    config = load_env_config(env_file)
    local_config = load_local_config(local_env_file)

    # 命令行 provider 优先级最高；否则回退到 .env 中的 provider（若存在）
    active_provider = provider or config.get("provider", "mcp")

    image_file, input_status, input_message = resolve_single_image(path)
    if input_status != "ok" or image_file is None:
        return {
            "result_status": input_status,
            "summary": input_message or "输入不是单张本地图像",
            "targets": [],
            "files_created": [],
        }

    # 确定输出目录
    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = image_file.parent

    # 创建输出目录
    if not out_dir.exists():
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {
                "result_status": "output_dir_create_failed",
                "summary": f"无法创建输出目录: {e}",
                "targets": [],
                "files_created": [],
            }

    result = process_image(image_file, out_dir, active_provider, config, local_config)
    target = {
        "source": str(image_file),
        "status": result.status,
        "message": result.message,
        "output_files": result.files_created,
        "details": result.details,
        "level": "per_item",
    }

    # 退出码契约：
    # - success: 退出码 0
    # - partial: 退出码 2（含 mcp_required、paddleocr_*、chandra_*、save_failed、process_image 路径下的 output_dir_create_failed 等非 success 状态码）
    # - 输入类 4 个 + run() 自身 output_dir_create_failed: 退出码 1
    if result.status == "success":
        result_status = "success"
        summary = f"成功处理 1 个图像；输出文件: {len(result.files_created)} 个"
    elif result.status == "mcp_required":
        result_status = "partial"
        summary = "需使用 MCP 1 个"
    else:
        result_status = "partial"
        summary = "失败 1 个"

    return {
        "result_status": result_status,
        "summary": summary,
        "targets": [target],
        "files_created": result.files_created,
    }


def main():
    parser = argparse.ArgumentParser(
        description="将单张本地图像 OCR 提取文字并保存为 Markdown（支持 MCP/远程 API/本地 OCR 三种模式）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # MCP 模式（默认）
  %(prog)s --input demo.png

  # 远程 API 模式
  %(prog)s --input demo.png --provider api

  # 本地 OCR 模式
  %(prog)s --input demo.png --provider local

        """
    )
    parser.add_argument("--input", required=True, help="输入单张本地图像文件（.png/.jpg/.jpeg/.webp）")
    parser.add_argument(
        "--provider",
        choices=["mcp", "api", "local"],
        default=None,
        help="OCR 模式：mcp（默认）/ api / local",
    )
    parser.add_argument("--env-file", help=".env 配置文件路径（远程 API 用，默认 ~/.config/zm-ocr2md/.env）")
    parser.add_argument("--local-env-file", help=".local_env 配置文件路径（本地 OCR 用，默认 ~/.config/zm-ocr2md/.local_env）")
    parser.add_argument("--output-dir", help="输出目录（默认写到源文件同目录）")
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果（供程序调用）",
    )

    args = parser.parse_args()

    result = run(args.input, args.env_file, args.local_env_file, args.provider, args.output_dir)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*50}")
        print(f"zm-ocr2md OCR 结果")
        print(f"{'='*50}")
        print(f"模式: {args.provider}")
        print(f"图像总数: {result.get('image_count', 0)}")
        print(f"状态: {result['result_status']}")
        print(f"汇总: {result['summary']}")
        if result.get("targets"):
            print(f"\n详细:")
            for t in result["targets"]:
                print(f"  来源: {t['source']}")
                print(f"  状态: {t['status']} - {t['message']}")
                if t["output_files"]:
                    print(f"  输出: {t['output_files'][0]}" + (" ..." if len(t["output_files"]) > 1 else ""))
                print()
        if result.get("files_created"):
            print(f"共生成 {len(result['files_created'])} 个 Markdown 文件")

    # 根据状态返回退出码；与 SKILL.md 状态码表的"退出码"列严格对齐
    # - 输入类 4 个状态码 + run() 自身的 output_dir_create_failed → 退出码 1
    # - partial（聚合：含 mcp_required、paddleocr_*、chandra_*、save_failed、
    #   process_image 路径下的 output_dir_create_failed 等非 success 状态码）→ 退出码 2
    # - success → 退出码 0
    if result["result_status"] in (
        "missing_input",
        "non_single_input",
        "unsupported_format",
    ):
        sys.exit(1)
    elif result["result_status"] == "output_dir_create_failed":
        # run() 自身 output_dir_create_failed 走 1
        sys.exit(1)
    elif result["result_status"] == "partial":
        sys.exit(2)
    elif result["result_status"] == "success":
        sys.exit(0)
    else:
        # 兜底：未知状态码按失败处理，退出码 1
        sys.exit(1)


if __name__ == "__main__":
    main()
