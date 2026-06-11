#!/usr/bin/env python3
"""
zm-batch-ocr2md: 将目录或清单中的本地图像 OCR 提取文字并保存为 Markdown
支持三种 OCR 模式：
  - mcp（默认）：MCP 视觉模型 understand_image，优先 minimax-coding-plan-mcp，失败时降级到 moonshot-vision
  - api：远程 API（OpenAI / Gemini / Anthropic / Azure 等）
  - local：本地 OCR（PaddleOCR / Chandra OCR 2）
支持两种输入方式：
  - 图像目录（不递归，扫描当前层所有图像）
  - 清单文件（.json/.txt），内含图像路径列表
"""

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
import unicodedata
import urllib.error
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

# MCP 模式批量大小：超过此数量自动分批以避免 API 精度下降
MCP_BATCH_SIZE = 30
DEFAULT_LOCAL_CONFIG = {
    # 公共参数
    "LOCAL_CACHE_DIR": "",
    # conda 环境名（默认 ocr，可由 .local_env 覆盖为 OCR_CONDA_ENV）（S-1）
    "OCR_CONDA_ENV": "ocr",
    # 引擎选择
    "LOCAL_ENGINE": "paddle",   # paddle | chandra
    # PaddleOCR 参数（结构化 env var，S-2）
    "PADDLE_LANG": "ch",
    "PADDLE_USE_TEXTLINE_ORIENTATION": "False",
    "PADDLE_USE_DOC_ORIENTATION_CLASSIFY": "False",
    "PADDLE_USE_DOC_UNWARPING": "False",
    "PADDLE_USE_SEAL_RECOGNITION": "False",
    "PADDLE_USE_TABLE_RECOGNITION": "False",
    "PADDLE_DEVICE": "cpu",
    "PADDLE_CPU_THREADS": "10",
    "PADDLE_PARAMS": "",  # 高级兜底，留空则由结构化 env var 合成
    # Chandra OCR 2 参数
    "CHANDRA_BACKEND": "hf",   # hf | vllm | docker
    "CHANDRA_VLLM_ENDPOINT": "http://localhost:8000/v1",
    "CHANDRA_DOCKER_ENDPOINT": "http://localhost:8501/v1",
}

CONFIG_PATH = Path.home() / ".config" / "zm-batch-ocr2md" / ".env"
LOCAL_CONFIG_PATH = Path.home() / ".config" / "zm-batch-ocr2md" / ".local_env"


class Result(NamedTuple):
    status: str
    message: str
    files_created: list[str]
    details: dict


def _path_hint(path: Path) -> str:
    """生成与图像路径绑定的短唯一后缀，用于 safe_filename 的 unnamed 兜底（P1-6）。"""
    import hashlib

    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]


def safe_filename(name: str, unique_hint: str | None = None) -> str:
    """将字符串转换为安全的文件系统名字。

    过滤规则：
    - 去除 Unicode 类别首位为 'C' 的字符（Cc 控制、Cf 格式、Cs 代理项、Co 私有使用、Cn 未分配）
    - 保留显式白名单字符 `_` 和 `-`
    - 去除 Windows/Unix 常见非法字符 `<>:"/\\|?*`
    - 去除首尾空格和句点
    - 内部空格转为下划线
    - 合并连续下划线
    - 全空时回退到 "unnamed"（若提供 unique_hint，则用 hint 区分多图同名冲突；P1-6）
    - Windows 保留名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）大小写不敏感，命中则前缀加 `_`（P0-4）
    """
    invalid_chars = '<>:"/\\|?*'
    cleaned = "".join(
        c for c in name
        if (unicodedata.category(c)[0] != "C" or c in ("_", "-"))
        and c not in invalid_chars
    ).rstrip(" .").replace(" ", "_")
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned:
        return f"unnamed-{unique_hint}" if unique_hint else "unnamed"
    upper = cleaned.upper()
    if upper in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"COM[1-9]|LPT[1-9]", upper):
        return f"_{cleaned}"
    return cleaned


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
    """解析 PaddleOCR 参数字符串为字典。

    支持 `--key=value` 与 `--flag` 两种形式。
    使用 shlex.split 严格处理引号与空白，避免 value 中含 `=` 或空格时被静默截断。
    未配对的引号会抛 ValueError，由上层暴露给用户。
    """
    params: dict = {}
    if not params_str:
        return params
    tokens = shlex.split(params_str)

    for token in tokens:
        if not token.startswith("--"):
            continue
        body = token[2:]
        if "=" in body:
            key, value = body.split("=", 1)
        else:
            key, value = body, "True"
        # 处理 Python 布尔值
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        elif value.lower() == "none":
            value = None
        params[key] = value
    return params


def _build_paddle_params_from_env(local_config: dict) -> dict:
    """从结构化 env var 合成 PaddleOCR 参数（S-2）。`PADDLE_PARAMS` 仍可作为高级兜底。"""
    bool_keys = [
        "PADDLE_USE_TEXTLINE_ORIENTATION",
        "PADDLE_USE_DOC_ORIENTATION_CLASSIFY",
        "PADDLE_USE_DOC_UNWARPING",
        "PADDLE_USE_SEAL_RECOGNITION",
        "PADDLE_USE_TABLE_RECOGNITION",
    ]
    params: dict = {}
    lang = local_config.get("PADDLE_LANG")
    if lang:
        params["lang"] = lang
    for key in bool_keys:
        raw = local_config.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(raw, bool):
            params[key[len("PADDLE_"):].lower()] = raw
        elif str(raw).lower() in {"true", "false"}:
            params[key[len("PADDLE_"):].lower()] = str(raw).lower() == "true"
    device = local_config.get("PADDLE_DEVICE")
    if device:
        params["device"] = device
    cpu_threads = local_config.get("PADDLE_CPU_THREADS")
    if cpu_threads:
        try:
            params["cpu_threads"] = int(cpu_threads)
        except (ValueError, TypeError):
            pass
    return params


def parse_manifest_json(path: Path) -> tuple[list[Path], str | None]:
    """解析 JSON 清单文件，提取 absolute_paths 数组中的有效图像路径。

    返回: (图像文件列表, 错误信息或 None)
    """
    try:
        # 使用 utf-8-sig 自动剥离 UTF-8 BOM（Windows 工具常见生成带 BOM 的 JSON）
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [], f"JSON 解析失败: {e}"
    except Exception as e:
        return [], f"读取清单文件失败: {e}"

    paths = data.get("absolute_paths", [])
    if not isinstance(paths, list):
        return [], "清单文件缺少 'absolute_paths' 数组或类型不正确"

    suffixes = {".png", ".jpg", ".jpeg", ".webp"}
    result = []
    seen = set()
    for p_str in paths:
        if not isinstance(p_str, str):
            continue
        p = Path(p_str)
        # is_file() 隐含 exists()，并把"以图像后缀结尾的目录"过滤掉（P0-1）
        if p.is_file() and p.suffix.lower() in suffixes and p not in seen:
            result.append(p)
            seen.add(p)

    return result, None


def parse_manifest_txt(path: Path) -> tuple[list[Path], str | None]:
    """解析 TXT 清单文件，逐行读取，跳过空行和 # 注释行。

    返回: (图像文件列表, 错误信息或 None)
    """
    try:
        suffixes = {".png", ".jpg", ".jpeg", ".webp"}
        result = []
        seen = set()
        # 使用 utf-8-sig 自动剥离 UTF-8 BOM，避免 Windows 工具生成的 BOM 让首行变成 ﻿ 路径
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                # NFKC 归一化避免 NBSP（\xa0）等不可见空白绕过 strip()（P2-2）
                line = unicodedata.normalize("NFKC", line).strip()
                if not line or line.startswith("#"):
                    continue
                p = Path(line)
                # is_file() 隐含 exists()，并把"以图像后缀结尾的目录"过滤掉（P0-1）
                if p.is_file() and p.suffix.lower() in suffixes and p not in seen:
                    result.append(p)
                    seen.add(p)
        return result, None
    except Exception as e:
        return [], f"读取清单文件失败: {e}"


def resolve_input(path: str, force_manifest: bool = False) -> tuple[list[Path], bool, str | None]:
    """解析输入路径：图像目录或清单文件（.json/.txt）。

    返回: (图像文件列表, 是否是清单文件, 错误信息或 None)
    """
    suffixes = {".png", ".jpg", ".jpeg", ".webp"}
    manifest_exts = {".json", ".txt"}
    p = Path(path)

    if not p.exists():
        return [], False, f"输入路径不存在: {path}"

    if p.is_file():
        ext = p.suffix.lower()
        if ext in suffixes:
            return [], False, "batch_input_required"
        elif ext in manifest_exts or force_manifest:
            if ext == ".json" or (force_manifest and ext != ".txt"):
                files, err = parse_manifest_json(p)
            else:
                files, err = parse_manifest_txt(p)
            return files, True, err
        else:
            return [], False, None
    elif p.is_dir():
        files = sorted(
            [f for f in p.iterdir() if f.suffix.lower() in suffixes and f.is_file()]
        )
        return files, False, None

    return [], False, None


def compute_batches(image_files: list[Path], batch_size: int) -> list[list[Path]]:
    """将图像列表拆分为多个批次。"""
    if not image_files:
        return []
    return [image_files[i:i + batch_size] for i in range(0, len(image_files), batch_size)]


def format_batch_info(batches: list[list[Path]], batch_size: int = MCP_BATCH_SIZE) -> str:
    """格式化分批信息为可读字符串。"""
    if not batches:
        return ""
    lines = [f"MCP 模式将自动分批处理（每批 ≤{batch_size} 张）"]
    for i, batch in enumerate(batches, 1):
        names = [f.name for f in batch[:3]]
        if len(batch) > 3:
            suffix = f" ... (共 {len(batch)} 张)"
        elif len(batch) == 1:
            suffix = f" ({batch[0].name})"
        else:
            suffix = ""
        lines.append(f"  第 {i} 批：{len(batch)} 张 [{', '.join(names)}{suffix}]")
    return "\n".join(lines)


def serialize_batches(batches: list[list[Path]]) -> list[list[str]]:
    """Return JSON-serializable batch paths while preserving batch order."""
    return [[str(path) for path in batch] for batch in batches]


def ocr_with_mcp(image_path: Path) -> Result:
    """使用 MCP 视觉模型的 understand_image 工具：优先 minimax-coding-plan-mcp，失败时降级到 moonshot-vision。"""
    return Result(
        status="mcp_required",
        message="请使用 MCP 视觉模型 understand_image：优先 minimax-coding-plan-mcp，失败或不可用时降级到 moonshot-vision",
        files_created=[],
        details={"image": str(image_path), "provider": "mcp"},
    )


def _retry_request(make_request, max_attempts: int = 3) -> bytes:
    """对网络瞬时错误做指数退避重试：URLError / TimeoutError / HTTP 5xx；4xx 不重试（P1-4）。

    make_request 是一个无参 callable，返回响应 body bytes。
    返回最后一次调用的 body，或抛出最后一次异常。
    """
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return make_request()
        except urllib.error.HTTPError as e:
            last_exc = e
            if 500 <= e.code < 600 and attempt < max_attempts:
                time.sleep(0.5 * (2 ** (attempt - 1)))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last_exc = e
            if attempt < max_attempts:
                time.sleep(0.5 * (2 ** (attempt - 1)))
                continue
            raise
    # 理论上不会到这里
    if last_exc:
        raise last_exc
    return b""


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
            message="API_KEY 未配置，请检查 ~/.config/zm-batch-ocr2md/.env",
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

    # 根据文件扩展名确定 mime 类型
    suffix = image_path.suffix.lower()
    if suffix == ".png":
        mime_type = "image/png"
    elif suffix in {".jpg", ".jpeg"}:
        mime_type = "image/jpeg"
    elif suffix == ".webp":
        mime_type = "image/webp"
    else:
        return Result(
            status="unsupported_format",
            message=f"不支持的图像格式: {suffix}",
            files_created=[],
            details={"image": str(image_path)},
        )

    # 强制 https：明文 HTTP 会被拦截以避免 API_KEY 泄露；本地服务 (localhost/127.0.0.1) 例外（C-2）
    lower_base = api_base.lower()
    if lower_base.startswith("http://") and not (
        lower_base.startswith("http://localhost") or lower_base.startswith("http://127.0.0.1")
    ):
        return Result(
            status="openai_api_failed",
            message=f"API_BASE 必须使用 https://；当前为 {api_base}，本地服务除外",
            files_created=[],
            details={"image": str(image_path)},
        )

    # max_tokens 配置读取（带空值保护）
    max_tokens_raw = config.get("MAX_TOKENS", 8192)
    try:
        max_tokens = int(max_tokens_raw) if max_tokens_raw else 8192
    except (ValueError, TypeError):
        max_tokens = 8192

    # 构建请求
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "请提取这张图片中的所有文字，以 Markdown 格式输出。只输出文字内容，不要其他解释。",
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

    def _do_request():
        """执行实际 HTTP 调用；供 _retry_request 包装重试（P1-4）"""
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read()

    try:
        body = _retry_request(_do_request)
        result_data = json.loads(body.decode("utf-8"))

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


def _chandra_api_config(endpoint: str, model: str) -> dict:
    """为 chandra vllm / docker 后端构造与 ocr_with_api 兼容的本地 API 配置。"""
    return {
        "API_BASE": endpoint,
        "API_KEY": "",
        "API_MODEL": model,
    }


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
    use_textline_orientation = params.get("use_textline_orientation", False)
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

    # 检查模型缓存（PaddleOCR 3.4+ 默认模型缓存可能在 ~/.paddlex/official_models 或 ~/.paddleocr）
    paddle_cache_paths = [
        Path.home() / ".paddlex" / "official_models",
        Path.home() / ".paddleocr",
    ]
    for cache_path in paddle_cache_paths:
        if cache_path.exists() and any(cache_path.iterdir()):
            env["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
            break

    # 创建临时脚本和临时文件
    fd, tmp_script_path = _tempfile.mkstemp(suffix="_paddle_ocr.py", prefix="zm-batch-ocr2md_")
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
            fd_img, tmp_img_path = _tempfile.mkstemp(suffix=image_path.suffix, prefix="zm-batch-ocr2md_paddle_img_")
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

    # 构建脚本内容
    script_content = f'''
import sys
import json
from pathlib import Path

try:
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(use_textline_orientation={str(use_textline_orientation)}, lang="{lang}")
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
            ["conda", "run", "-n", (local_config or {}).get("OCR_CONDA_ENV", "ocr"), "python", str(tmp_script)],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )

        # 解析输出
        if result.returncode == 0 and result.stdout.strip():
            try:
                ocr_data = _json.loads(result.stdout.strip())
                status = ocr_data.get("status")
                if status == "success":
                    return Result(
                        status="success",
                        message="PaddleOCR 成功",
                        files_created=[],
                        details={"image": str(image_path), "text": ocr_data.get("text", "")},
                    )
                elif status == "empty":
                    return Result(
                        status="paddleocr_failed",
                        message="PaddleOCR 未检测到文字",
                        files_created=[],
                        details={"image": str(image_path)},
                    )
                else:
                    # 兜底 returncode=0 但 status 异常（如 "error" / "import_error"），
                    # 不能放过，必须显式 paddleocr_failed
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
                message=f"PaddleOCR 导入失败，请检查 conda 环境 {(local_config or {}).get('OCR_CONDA_ENV', 'ocr')}",
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


def ocr_with_chandra(image_path: Path, output_dir: Path, method: str = "hf", conda_env: str = "ocr") -> Result:
    """使用 conda run -n ocr chandra --method hf|vllm <INPUT> <OUTPUT> 进行 OCR。

    策略：
    - HF 后端：优先检查模型缓存是否存在
      - 缓存存在 → 设置 HF_HUB_OFFLINE=1，强制使用本地缓存
      - 缓存不存在 → 允许联网下载模型
    - vLLM 后端：走 chandra CLI 调用本地 vLLM 服务（OpenAI 兼容协议）；Docker 后端不走此函数
    - 注意：chandra CLI 实际只支持 hf|vllm，不支持 docker；docker 后端由 process_image 走 ocr_with_api
    """
    # 准备环境变量
    env = os.environ.copy()

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
                "conda", "run", "-n", conda_env,
                "chandra",
                "--method", method,  # chandra CLI 仅支持 hf|vllm；docker 后端走 ocr_with_api 远程调用
                str(image_path),
                str(output_dir),
            ],
            capture_output=True,
            text=True,
            timeout=86400,
            env=env,
        )

        if result.returncode == 0:
            # Chandra OCR 2 成功，查找输出的 md 文件
            stem = safe_filename(image_path.stem, unique_hint=_path_hint(image_path))
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
            message="Chandra OCR 2 推理超时（>86400s）",
            files_created=[],
            details={"image": str(image_path)},
        )
    except FileNotFoundError:
        return Result(
            status="chandra_import_failed",
            message=f"conda 命令未找到（目标环境 {conda_env}），请确认已安装 conda",
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
    skip_existing: bool = True,
) -> Result:
    """处理单张图像：OCR → 保存 Markdown。

    skip_existing：默认 True；若 out_md 已存在且非空，直接返回 skipped（节省 OCR 调用）（S-3）。
    """
    ocr_result = Result(status="unknown", message="", files_created=[], details={})
    stem = safe_filename(image_path.stem, unique_hint=_path_hint(image_path))

    # 确定输出路径
    out_subdir = output_dir
    out_md = output_dir / f"{stem}.md"

    # 增量断点：out_md 已存在且非空时跳过（S-3）
    if skip_existing and out_md.exists() and out_md.stat().st_size > 0:
        return Result(
            status="success",
            message=f"已跳过：输出文件 {out_md} 已存在",
            files_created=[str(out_md)],
            details={"image": str(image_path), "skipped": True, "text": ""},
        )

    try:
        out_subdir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return Result(
            status="output_dir_create_failed",
            message=f"无法创建输出目录 {out_subdir}: {e}",
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
            # PaddleOCR：先由结构化 env var 合成（S-2），再用 PADDLE_PARAMS 高级兜底覆盖
            paddle_params = _build_paddle_params_from_env(local_config)
            params_str = local_config.get("PADDLE_PARAMS", "")
            if params_str:
                paddle_params.update(parse_paddle_params(params_str))
            # 允许 CLI 参数覆盖
            if "use_gpu" in local_config:
                paddle_params["use_gpu"] = local_config["use_gpu"]
            ocr_result = ocr_with_paddle(image_path, paddle_params, local_config)

        elif engine == "chandra":
            backend = local_config.get("CHANDRA_BACKEND", "hf")

            if backend == "hf":
                # HF 后端（通过 conda run 调用）
                import shutil as _shutil
                import tempfile as _tf
                temp_out = Path(_tf.mkdtemp(prefix="zm-batch-ocr2md_chandra_"))
                try:
                    chandra_result = ocr_with_chandra(
                        image_path,
                        temp_out,
                        method="hf",
                        conda_env=local_config.get("OCR_CONDA_ENV", "ocr"),
                    )

                    # 移动结果到标准位置：按 stem 过滤，多文件时优先取主结果（<stem>.md），
                    # 其余结构化文件（如 <stem>_layout.md / <stem>_table.md 等）一并复制到 output_dir 旁，
                    # 避免 layout/table/figure 结构化结果被静默丢弃。
                    if chandra_result.status == "success":
                        md_files = sorted(temp_out.glob(f"{stem}*.md"))
                        primary = temp_out / f"{stem}.md"
                        if not primary.exists() and md_files:
                            # 主结果缺失（chandra 偶发只输出 _layout/_table 等结构化文件），
                            # 从剩余 md_files 取第一个兜底，避免所有结果被静默丢失。
                            primary = md_files[0]

                        if md_files and primary.exists():
                            try:
                                content = primary.read_text(encoding="utf-8")
                                # 与其他 provider 落盘格式对齐：统一附加 `# {stem}` 标题
                                out_md.write_text(f"# {stem}\n\n{content}\n", encoding="utf-8")
                                extras = []
                                for extra in md_files:
                                    if extra == primary:
                                        continue
                                    dest = out_md.parent / extra.name
                                    dest.write_text(extra.read_text(encoding="utf-8"), encoding="utf-8")
                                    extras.append(str(dest))
                                files_created = [str(out_md)] + extras
                                message = chandra_result.message
                                if primary.name != f"{stem}.md":
                                    message += f"（主结果缺失，已用 {primary.name} 兜底）"
                                if extras:
                                    message += f"（已同时输出 {len(extras)} 个结构化文件）"
                                chandra_result = Result(
                                    status="success",
                                    message=message,
                                    files_created=files_created,
                                    details=chandra_result.details,
                                )
                            except Exception as e:
                                chandra_result = Result(
                                    status="save_failed",
                                    message=f"保存 Markdown 失败: {e}",
                                    files_created=[],
                                    details={"image": str(image_path)},
                                )
                        else:
                            # chandra 返回 success 但 temp_out 没有任何 md 文件，
                            # 必须显式报失败，不能让 success 通过。
                            chandra_result = Result(
                                status="chandra_inference_failed",
                                message="Chandra OCR 2 (hf) 成功退出但未生成任何 Markdown 文件",
                                files_created=[],
                                details={"image": str(image_path), "method": "hf"},
                            )
                finally:
                    # 不论 chandra_result.status 如何、是否抛异常，临时目录都清理（P0-3）
                    try:
                        _shutil.rmtree(temp_out, ignore_errors=True)
                    except Exception:
                        pass
                ocr_result = chandra_result

            elif backend == "vllm":
                # vLLM 后端（通过 API 调用）
                local_api_config = _chandra_api_config(
                    local_config.get("CHANDRA_VLLM_ENDPOINT", "http://localhost:8000/v1"),
                    local_config.get("CHANDRA_VLLM_MODEL", "chandra-ocr-2"),
                )
                ocr_result = ocr_with_api(image_path, local_api_config, require_api_key=False)

            elif backend == "docker":
                # Docker 后端（通过 API 调用）
                local_api_config = _chandra_api_config(
                    local_config.get("CHANDRA_DOCKER_ENDPOINT", "http://localhost:8501/v1"),
                    local_config.get("CHANDRA_DOCKER_MODEL", "chandra-ocr-2"),
                )
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




def generate_batch_jsons(image_files, input_dir, group_size, other_args, provider=None):
    """将图片列表分组成多个 JSON 文件，生成续跑提示和进度跟踪文件。"""
    from datetime import datetime
    batches_dir = input_dir / "batches"
    batches_dir.mkdir(exist_ok=True)

    # 清理旧的 batch 文件
    for old in batches_dir.glob("batch_*.json"):
        old.unlink()

    batches = [image_files[i:i + group_size] for i in range(0, len(image_files), group_size)]
    json_paths = []
    prompts = []
    script_path = Path(__file__).resolve()
    batches_info = []

    for idx, batch in enumerate(batches, 1):
        json_path = batches_dir / f"batch_{idx:03d}.json"
        output_dir = batches_dir / f"batch_{idx:03d}_ocr2md"
        payload = {
            "absolute_paths": [str(p) for p in batch],
            "_batch_meta": {
                "skill": "zm-batch-ocr2md",
                "batch_index": idx,
                "total_batches": len(batches),
                "group_size": group_size,
                "generated_from": str(input_dir),
                "generated_at": datetime.now().isoformat(),
            },
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        json_paths.append(json_path)
        prompts.append(build_resume_prompt(json_path, batch, other_args, script_path, total_batches=len(batches)))
        batches_info.append({
            "batch_index": idx,
            "batch_file": str(json_path),
            "image_count": len(batch),
            "status": "pending",
            "output_dir": str(output_dir),
        })

    # 生成 progress.json
    progress_path = generate_progress_json(
        batches_dir, input_dir, group_size, provider, batches_info
    )

    return json_paths, prompts, progress_path


def generate_progress_json(batches_dir, source_dir, group_size, provider, batches_info):
    """生成 progress.json 进度跟踪文件。"""
    from datetime import datetime
    progress_path = batches_dir / "progress.json"
    total_images = sum(b["image_count"] for b in batches_info)
    progress_data = {
        "skill": "zm-batch-ocr2md",
        "version": "0.2.0",
        "stage": "prepared",
        "source_dir": str(source_dir),
        "total_batches": len(batches_info),
        "total_images": total_images,
        "group_size": group_size,
        "provider": provider or "mcp",
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "batches": batches_info,
    }
    progress_path.write_text(json.dumps(progress_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return progress_path


def load_progress_json(path):
    """读取 progress.json，验证字段完整性。

    返回: (progress_data, error_message_or_None)
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"progress.json 解析失败: {e}"
    except Exception as e:
        return None, f"读取 progress.json 失败: {e}"

    required_keys = {"batches", "total_batches", "source_dir"}
    missing = required_keys - set(data.keys())
    if missing:
        return None, f"progress.json 缺少必要字段: {', '.join(missing)}"

    if not isinstance(data.get("batches"), list):
        return None, "progress.json 'batches' 字段必须是数组"

    return data, None


def update_progress_batch(progress_path, batch_index, status, details=None):
    """原子更新 progress.json 中指定 batch 的状态。

    先写临时文件再重命名，避免写入中断导致文件损坏。
    """
    try:
        data, err = load_progress_json(progress_path)
        if err or data is None:
            return

        for batch in data.get("batches", []):
            if batch.get("batch_index") == batch_index:
                batch["status"] = status
                if details:
                    batch.update(details)
                break

        # 检查是否全部完成
        all_done = all(
            b.get("status") == "completed" for b in data.get("batches", [])
        )
        if all_done and data.get("batches"):
            from datetime import datetime
            data["stage"] = "completed"
            data["completed_at"] = datetime.now().isoformat()

        temp_path = Path(str(progress_path) + ".tmp")
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(progress_path)
    except Exception:
        # 进度更新失败不应阻断主流程，静默忽略
        pass


def process_batches_from_progress(progress_data, env_file, local_env_file, force, override_provider=None):
    """从 progress.json 中读取所有 pending/failed batch，单会话顺序处理。

    返回: dict（与 run() 的返回结构兼容）
    """
    config = load_env_config(env_file)
    local_config = load_local_config(local_env_file)
    # 命令行显式指定的 provider 优先覆盖 progress.json 中记录的值
    provider = override_provider or progress_data.get("provider", "mcp")

    pending_batches = [
        b for b in progress_data.get("batches", [])
        if b.get("status") != "completed"
    ]

    if not pending_batches:
        return {
            "result_status": "all_completed",
            "summary": f"所有 {progress_data.get('total_batches', 0)} 个批次已处理完成",
            "targets": [],
            "files_created": [],
            "image_count": progress_data.get("total_images", 0),
            "batches_total": progress_data.get("total_batches", 0),
            "batches_processed": 0,
        }

    progress_path = Path(progress_data.get("_progress_path", ""))
    all_targets = []
    all_created = []
    total_images_processed = 0
    batch_success_count = 0
    batch_fail_count = 0

    for batch_info in pending_batches:
        batch_index = batch_info["batch_index"]
        batch_file = Path(batch_info["batch_file"])
        output_dir = Path(batch_info["output_dir"])

        # 解析 batch JSON 获取图片路径
        image_files, is_manifest, manifest_err = resolve_input(str(batch_file), force_manifest=True)
        if manifest_err or not image_files:
            # batch JSON 解析失败，标记为失败并继续
            update_progress_batch(progress_path, batch_index, "failed", {
                "error": manifest_err or "batch 无有效图像"
            })
            batch_fail_count += 1
            continue

        # 创建输出目录
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            update_progress_batch(progress_path, batch_index, "failed", {
                "error": f"无法创建输出目录: {e}"
            })
            batch_fail_count += 1
            continue

        # 处理该 batch 中的每张图片
        batch_targets = []
        batch_created = []
        batch_has_failure = False

        for image_file in image_files:
            result = process_image(
                image_file, output_dir, provider, config, local_config,
                skip_existing=not force,
            )
            batch_targets.append({
                "source": str(image_file),
                "status": result.status,
                "message": result.message,
                "output_files": result.files_created,
                "details": result.details,
            })
            batch_created.extend(result.files_created)
            total_images_processed += 1
            if result.status not in ("success", "mcp_required"):
                batch_has_failure = True

        all_targets.extend(batch_targets)
        all_created.extend(batch_created)

        # 标记 batch 状态
        if batch_has_failure:
            update_progress_batch(progress_path, batch_index, "failed")
            batch_fail_count += 1
        else:
            update_progress_batch(progress_path, batch_index, "completed")
            batch_success_count += 1

    # 聚合结果
    success_count = sum(1 for t in all_targets if t["status"] == "success")
    mcp_count = sum(1 for t in all_targets if t["status"] == "mcp_required")
    failed_count = len(all_targets) - success_count - mcp_count

    summary_parts = []
    summary_parts.append(f"续跑完成：共处理 {len(pending_batches)} 个批次")
    if batch_success_count > 0:
        summary_parts.append(f"成功 {batch_success_count} 个批次")
    if batch_fail_count > 0:
        summary_parts.append(f"失败 {batch_fail_count} 个批次")
    if success_count > 0:
        summary_parts.append(f"成功处理 {success_count} 个图像")
    if failed_count > 0:
        summary_parts.append(f"失败 {failed_count} 个图像")
    if mcp_count > 0:
        summary_parts.append(f"需使用 MCP {mcp_count} 个")
    if all_created:
        summary_parts.append(f"输出文件: {len(all_created)} 个")

    return {
        "result_status": (
            "resume_completed"
            if failed_count == 0
            else "partial"
        ),
        "summary": "；".join(summary_parts),
        "targets": all_targets,
        "files_created": all_created,
        "image_count": total_images_processed,
        "batches_total": progress_data.get("total_batches", 0),
        "batches_processed": len(pending_batches),
    }


def build_resume_prompt(json_path, batch, other_args, script_path, total_batches=None):
    """构建单条简体中文续跑提示（含完整命令）。"""
    batch_idx = int(json_path.stem.split("_")[1])
    # 拼接其他参数
    args_str = " ".join(other_args) if other_args else ""
    cmd_single = f'conda run -n ocr python "{script_path}" --input "{json_path}"{args_str}'
    header = (
        f"【批次 {batch_idx} / 共 {total_batches} 批次】"
        if total_batches is not None
        else f"【批次 {batch_idx}】"
    )
    return f"""{header}
输入分组文件: {json_path}
包含图片数: {len(batch)} 张

以下命令请在【新会话】中执行，第一阶段到此结束：

方式一（单会话续跑所有批次）：
conda run -n ocr python "{script_path}" --resume "{json_path.parent}"{args_str}

方式二（仅处理该批次）：
{cmd_single}

处理完成后，该批次的 Markdown 输出将位于对应输出目录。
所有批次相互独立，可并行执行。"""

def run(
    path: str,
    env_file: str | None,
    local_env_file: str | None,
    provider: str,
    output_dir: str | None,
    batch_size: int = MCP_BATCH_SIZE,
    force_manifest: bool = False,
    group_size: int = 10,
    force: bool = False,
    resume: str | None = None,
) -> dict:
    """核心运行函数。"""
    # ─── 续跑分支：--resume 优先处理 ───
    if resume:
        resume_p = Path(resume)
        # 支持传入目录（自动查找 progress.json）或 progress.json 文件本身
        if resume_p.is_dir():
            progress_path = resume_p / "progress.json"
        else:
            progress_path = resume_p

        if not progress_path.exists():
            return {
                "result_status": "missing_input",
                "summary": f"进度文件不存在: {progress_path}",
                "targets": [],
            }

        progress_data, err = load_progress_json(progress_path)
        if err or progress_data is None:
            return {
                "result_status": "manifest_parse_failed",
                "summary": f"进度文件解析失败: {err}",
                "targets": [],
            }

        # 注入 progress_path 以便 update_progress_batch 使用
        progress_data["_progress_path"] = str(progress_path)
        return process_batches_from_progress(progress_data, env_file, local_env_file, force, override_provider=provider)

    config = load_env_config(env_file)
    local_config = load_local_config(local_env_file)

    # 命令行 provider 优先级最高；否则回退到 .env 中的 provider（若存在）
    active_provider = provider or config.get("provider", "mcp")

    path_p = Path(path)

    # 预检
    if not path_p.exists():
        return {
            "result_status": "missing_input",
            "summary": f"输入路径不存在: {path}",
            "targets": [],
        }

    image_files, is_manifest, manifest_err = resolve_input(path, force_manifest)

    if manifest_err == "batch_input_required":
        return {
            "result_status": "batch_input_required",
            "summary": "zm-batch-ocr2md 仅接受当前层图片目录或 manifest 清单，不接受单张图片输入",
            "targets": [],
            "files_created": [],
            "image_count": 0,
        }

    # 清单文件解析失败
    if is_manifest and manifest_err:
        return {
            "result_status": "manifest_parse_failed",
            "summary": f"清单文件解析失败: {manifest_err}",
            "targets": [],
        }

    # 清单文件为空
    if is_manifest and not image_files:
        return {
            "result_status": "manifest_empty",
            "summary": f"清单文件中无有效图像路径: {path}",
            "targets": [],
        }

    if path_p.is_file() and not image_files and not is_manifest:
        return {
            "result_status": "unsupported_format",
            "summary": f"不支持的文件格式: {path}",
            "targets": [],
        }

    if path_p.is_dir() and not image_files:
        return {
            "result_status": "empty_input_dir",
            "summary": f"目录中没有图像文件: {path}",
            "targets": [],
        }

    # 第一阶段：目录输入时生成 batch JSON + 续跑提示 + progress.json
    if not is_manifest and group_size > 0 and path_p.is_dir():
        json_paths, prompts, progress_path = generate_batch_jsons(
            image_files, path_p, group_size, [], provider=active_provider
        )
        return {
            "result_status": "batch_prepared",
            "summary": f"已生成 {len(json_paths)} 个分组文件，共 {len(image_files)} 张图片",
            "input_dir": str(path_p),
            "batches_dir": str(path_p / "batches"),
            "total_images": len(image_files),
            "group_size": group_size,
            "total_batches": len(json_paths),
            "batch_files": [str(p) for p in json_paths],
            "progress_file": str(progress_path),
            "resume_prompts": prompts,
            "targets": [],
            "files_created": [str(p) for p in json_paths] + [str(progress_path)],
        }

    # 确定输出目录
    if output_dir:
        out_dir = Path(output_dir)
    elif is_manifest:
        out_dir = path_p.parent / f"{path_p.stem}_ocr2md"
    elif path_p.is_file():
        out_dir = path_p.parent
    else:
        out_dir = path_p.parent / f"{path_p.name}_ocr2md"

    # 显式拒绝"output_dir 路径已存在但是文件"（P1-5），让用户拿到的错误信息更直接
    if out_dir.exists() and not out_dir.is_dir():
        return {
            "result_status": "output_dir_create_failed",
            "summary": f"output_dir 路径已存在但不是目录: {out_dir}",
            "targets": [],
        }

    # 创建输出目录
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {
            "result_status": "output_dir_create_failed",
            "summary": f"无法创建输出目录: {e}",
            "targets": [],
        }

    # MCP 模式批量处理
    is_mcp = active_provider == "mcp"
    batch_info_lines = ""

    # 抽出 MCP 模式批量提示触发条件，避免在多处重复
    need_batch_info = is_mcp and len(image_files) > batch_size

    if need_batch_info:
        batches = compute_batches(image_files, batch_size)
        batch_info_lines = format_batch_info(batches, batch_size)
        # 注意：以下"分批"仅为信息性提示，方便 AI Agent 按批组织 MCP 调用；
        # 实际 OCR 处理仍按下方 for 循环逐图进行（每个目标独立返回 mcp_required），
        # 不会因为分批而把多个图像合并为一次 MCP 工具调用。

    # 处理（按图像逐条进行；MCP 模式下每条返回 mcp_required，由 Agent 接力）
    targets = []
    all_created = []

    for image_file in image_files:
        result = process_image(
            image_file, out_dir, active_provider, config, local_config,
            skip_existing=not force,
        )
        targets.append({
            "source": str(image_file),
            "status": result.status,
            "message": result.message,
            "output_files": result.files_created,
            "details": result.details,
        })
        all_created.extend(result.files_created)

    # 汇总
    success_count = sum(1 for t in targets if t["status"] == "success")
    mcp_count = sum(1 for t in targets if t["status"] == "mcp_required")
    failed_count = len(targets) - success_count - mcp_count

    summary_parts = []
    if success_count > 0:
        summary_parts.append(f"成功处理 {success_count} 个图像")
    if failed_count > 0:
        summary_parts.append(f"失败 {failed_count} 个")
    if mcp_count > 0:
        summary_parts.append(f"需使用 MCP {mcp_count} 个")
    if all_created:
        summary_parts.append(f"输出文件: {len(all_created)} 个")
    if batch_info_lines:
        summary_parts.append(f"\n{batch_info_lines}")

    return {
        # 聚合规则：
        #   全部 success                 -> "success"   退出码 0
        #   全部 mcp_required（无失败）   -> "mcp_only"  退出码 0（由 Agent 接力处理，不是失败）
        #   success 与 mcp_required 混合  -> "mcp_only"  退出码 0（MCP 接力是设计内的行为，不该让 Agent 误判）
        #   含任何失败                    -> "partial"   退出码 2
        "result_status": (
            "success"
            if failed_count == 0 and mcp_count == 0
            else "mcp_only"
            if failed_count == 0 and mcp_count > 0
            else "partial"
        ),
        "summary": "；".join(summary_parts) if summary_parts else "完成",
        "targets": targets,
        "files_created": all_created,
        "image_count": len(image_files),
        "batch_info": batch_info_lines if need_batch_info else "",
        "batches": serialize_batches(compute_batches(image_files, batch_size)) if need_batch_info else [],
    }


def main():
    parser = argparse.ArgumentParser(
        description="将目录或清单中的本地图像 OCR 提取文字并保存为 Markdown（支持 MCP/远程 API/本地 OCR 三种模式）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 目录输入，当前层不递归
  %(prog)s --input ./pages

  # 清单文件输入（.json 或 .txt）
  %(prog)s --input batch_1.json --provider local
  %(prog)s --input batch_1.txt --provider local
        """
    )
    parser.add_argument("--input", help="输入图像目录、或清单文件（.json/.txt）；不接受单张图片；与 --resume 二选一必填")
    parser.add_argument(
        "--provider",
        choices=["mcp", "api", "local"],
        default=None,
        help="OCR 模式：mcp（默认）/ api / local",
    )
    parser.add_argument("--env-file", help=".env 配置文件路径（远程 API 用，默认 ~/.config/zm-batch-ocr2md/.env）")
    parser.add_argument("--local-env-file", help=".local_env 配置文件路径（本地 OCR 用，默认 ~/.config/zm-batch-ocr2md/.local_env）")
    parser.add_argument("--output-dir", help="输出根目录（默认写到 <输入名>_ocr2md/）")
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果（供程序调用）",
    )
    parser.add_argument(
        "--manifest",
        action="store_true",
        help="将 --input 指定的文件视为清单文件（.json/.txt）",
    )

    def _non_negative_int(value: str) -> int:
        """--group-size 自定义 type：必须 >= 0；0 表示禁用分组（P1-1）"""
        try:
            ivalue = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"必须是整数，收到 {value!r}")
        if ivalue < 0:
            raise argparse.ArgumentTypeError(f"--group-size 必须 >= 0，收到 {value!r}")
        return ivalue

    def _positive_int(value: str) -> int:
        """--batch-size 自定义 type：必须 >= 1（P1-1）"""
        try:
            ivalue = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"必须是整数，收到 {value!r}")
        if ivalue < 1:
            raise argparse.ArgumentTypeError(f"--batch-size 必须 >= 1，收到 {value!r}")
        return ivalue

    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=MCP_BATCH_SIZE,
        help=f"MCP 模式每批最大图片数（默认 {MCP_BATCH_SIZE}）；必须 >= 1",
    )
    parser.add_argument(
        "--group-size",
        type=_non_negative_int,
        default=10,
        help="目录输入时，每个分组 JSON 文件包含的最大图片数（默认 10）；设为 0 则直接处理；必须 >= 0",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重跑：忽略 out_md 是否已存在（默认跳过已生成的图像以节省 OCR 调用；S-3）",
    )
    parser.add_argument(
        "--resume",
        metavar="PATH",
        help="续跑模式：传入 progress.json 文件路径或其所在目录，自动处理所有未完成的批次",
    )

    args = parser.parse_args()

    # --input 与 --resume 二选一必填
    if not args.input and not args.resume:
        parser.error("--input 与 --resume 至少必填一个")

    # --resume 模式下忽略 --input
    input_path = args.input if not args.resume else ""

    result = run(
        input_path, args.env_file, args.local_env_file, args.provider,
        args.output_dir, args.batch_size, args.manifest,
        getattr(args, 'group_size', 10), args.force,
        getattr(args, 'resume', None),
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*50}")
        print(f"zm-batch-ocr2md OCR 结果")
        print(f"{'='*50}")
        print(f"模式: {args.provider}")
        print(f"图像总数: {result.get('image_count', 0)}")
        print(f"状态: {result['result_status']}")
        # 显示分批信息（MCP 模式且超过批量限制时）
        if result.get("batch_info"):
            print(f"\n{result['batch_info']}")
        # 第一阶段（目录输入生成分组 JSON）必须把 resume_prompts 打印到 stdout，
        # 否则非 --json 模式下 Agent 拿不到续跑命令，可用性硬阻断。
        if result.get("result_status") == "batch_prepared" and result.get("resume_prompts"):
            print(f"\n{'='*50}")
            print(f"分组文件: {result.get('batches_dir')}")
            progress_file = result.get("progress_file")
            if progress_file:
                print(f"进度文件: {progress_file}")
            print(f"共 {len(result['resume_prompts'])} 个批次，请按下列提示执行每个批次：\n")
            for idx, prompt in enumerate(result["resume_prompts"], 1):
                print(f"--- 批次 {idx} ---")
                print(prompt)
                print()
        # 续跑模式的进度汇总
        if result.get("result_status") in ("resume_completed", "all_completed"):
            batches_total = result.get("batches_total", 0)
            batches_processed = result.get("batches_processed", 0)
            if batches_total > 0:
                print(f"\n批次进度: {batches_processed}/{batches_total}")
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

    # 根据状态返回退出码
    if result["result_status"] in (
        "missing_input",
        "output_dir_create_failed",
        "manifest_parse_failed",
        "manifest_empty",
        "batch_input_required",
        "unsupported_format",
        "empty_input_dir",
    ):
        sys.exit(1)
    elif result["result_status"] == "partial":
        # 至少含一个失败目标，或 success 与 mcp_required 混合
        sys.exit(2)
    else:
        # success / mcp_only / batch_prepared / resume_completed / all_completed 均视为 0
        sys.exit(0)


if __name__ == "__main__":
    main()
