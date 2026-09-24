"""표지 자동 생성 (기획안 43번).

    입력: 장르 / 주인공 / 세계관 / 분위기 / 제목
    출력: cover.jpg / thumbnail.jpg

순서
  1. 이미지 프롬프트를 만든다. LLM이 있으면 작품 설정으로 영어 프롬프트를 쓰게 하고,
     없으면 장르·분위기로 틀에 맞춘 프롬프트를 쓴다.
  2. 이미지 서버가 설정돼 있으면 그림을 받는다 (OpenAI 호환 또는 A1111 API).
  3. 그림 위에 제목과 작가명을 얹는다. 이미지 모델은 한글을 제대로 못 그리므로
     글자는 모델에게 맡기지 않고 여기서 직접 쓴다.
  4. 이미지 서버가 없거나 실패하면 그림 없이 글자 표지를 만든다.

참고소설의 표지를 복제하지 않는다 (기획안 43번). 이 모듈은 참고작 정보를 아예 읽지
않고, 프롬프트에도 기존 작품·작가를 흉내 내지 말라고 적는다.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx
from sqlalchemy.orm import Session

from novel_factory.config import Settings, get_settings
from novel_factory.database.models import Novel
from novel_factory.database.repositories import CharacterRepository, WorldRepository
from novel_factory.errors import LLMError, MissingDependencyError
from novel_factory.generation.prompts import COVER_SYSTEM, build_cover_prompt
from novel_factory.generation.schemas import CoverPromptOut
from novel_factory.generation.structured import StructuredOutputError, request_structured
from novel_factory.llm.base import LLMProvider, Message
from novel_factory.llm.openai_compat import post_json_with_retry
from novel_factory.publishing.info import get_info

if TYPE_CHECKING:
    from PIL import Image as PILImage

#: 한글 글꼴을 찾아볼 곳. 앞에 있는 것부터 쓴다.
FONT_CANDIDATES: tuple[str, ...] = (
    "/usr/share/fonts/truetype/nanum/NanumMyeongjoBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/Library/Fonts/NanumGothic.ttf",
)

# 틀 프롬프트에 쓰는 장르 영어 표현. 없는 장르는 장르명을 그대로 쓴다.
GENRE_EN: dict[str, str] = {
    "현대판타지": "modern urban fantasy, contemporary city",
    "판타지": "epic high fantasy",
    "무협": "wuxia martial arts, ancient east asian landscape",
    "로맨스": "romance, soft emotional lighting",
    "로맨스판타지": "romance fantasy, elegant palace",
    "SF": "science fiction, futuristic",
    "미스터리": "mystery thriller, noir lighting",
    "스릴러": "thriller, tense cinematic lighting",
    "라이트노벨": "anime style light novel illustration",
}

# 글자 표지 바탕색 (위, 아래). 작품마다 slug 해시로 하나를 고른다.
PALETTES: tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...] = (
    ((22, 28, 48), (86, 42, 70)),
    ((12, 40, 46), (20, 96, 92)),
    ((40, 24, 18), (138, 72, 38)),
    ((18, 18, 22), (70, 70, 88)),
    ((34, 20, 52), (112, 60, 120)),
    ((16, 36, 24), (70, 110, 60)),
)


class ImageClient(Protocol):
    def generate(
        self, prompt: str, negative_prompt: str, width: int, height: int
    ) -> bytes: ...


def _decode_b64(value: str) -> bytes:
    if value.startswith("data:"):
        value = value.split(",", 1)[1]
    return base64.b64decode(value)


class OpenAIImageClient:
    """OpenAI 호환 /v1/images/generations."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        timeout: float = 600.0,
        max_retries: int = 1,
        client: httpx.Client | None = None,
    ) -> None:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = client or httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout
        )
        self.model = model
        self.max_retries = max_retries

    def generate(self, prompt: str, negative_prompt: str, width: int, height: int) -> bytes:
        payload: dict[str, object] = {
            "prompt": prompt
            if not negative_prompt
            else f"{prompt}\nAvoid: {negative_prompt}",
            "n": 1,
            "size": f"{width}x{height}",
            "response_format": "b64_json",
        }
        if self.model:
            payload["model"] = self.model
        data = post_json_with_retry(
            self.client, "/images/generations", payload, self.max_retries
        )
        items = data.get("data") or []
        if not items:
            raise LLMError(f"이미지 응답에 data가 없습니다: {str(data)[:200]}")
        item = items[0]
        if item.get("b64_json"):
            return _decode_b64(item["b64_json"])
        if item.get("url"):
            response = self.client.get(item["url"])
            response.raise_for_status()
            return response.content
        raise LLMError(f"이미지 응답에 b64_json도 url도 없습니다: {str(item)[:200]}")

    def close(self) -> None:
        self.client.close()


class A1111ImageClient:
    """Stable Diffusion WebUI 계열 /sdapi/v1/txt2img."""

    def __init__(
        self,
        base_url: str,
        *,
        steps: int = 30,
        timeout: float = 600.0,
        max_retries: int = 1,
        client: httpx.Client | None = None,
    ) -> None:
        self.client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self.steps = steps
        self.max_retries = max_retries

    def generate(self, prompt: str, negative_prompt: str, width: int, height: int) -> bytes:
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": self.steps,
            "batch_size": 1,
        }
        data = post_json_with_retry(
            self.client, "/sdapi/v1/txt2img", payload, self.max_retries
        )
        images = data.get("images") or []
        if not images:
            raise LLMError(f"이미지 응답에 images가 없습니다: {str(data)[:200]}")
        return _decode_b64(images[0])

    def close(self) -> None:
        self.client.close()


def get_image_client(settings: Settings | None = None) -> ImageClient | None:
    cfg = settings or get_settings()
    if not cfg.image_base_url:
        return None
    if cfg.image_api == "a1111":
        # A1111 주소는 /sdapi 앞까지다 (예: http://localhost:7860).
        return A1111ImageClient(
            cfg.image_base_url, steps=cfg.image_steps, timeout=cfg.image_timeout_sec
        )
    return OpenAIImageClient(
        cfg.image_base_url,
        cfg.image_model,
        api_key=cfg.image_api_key,
        timeout=cfg.image_timeout_sec,
    )


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------
def _cover_inputs(session: Session, novel: Novel) -> tuple[dict[str, object] | None, list]:
    chars = CharacterRepository(session).for_novel(novel.id)
    hero = next((c for c in chars if c.role == "주인공"), chars[0] if chars else None)
    protagonist = None
    if hero is not None:
        protagonist = {
            "role": hero.role,
            "gender": hero.gender,
            "age": hero.age,
            "job": hero.job,
            "appearance": hero.appearance,
        }
    world = [
        {"category": w.category, "name": w.name, "description": w.description}
        for w in WorldRepository(session).for_novel(novel.id)[:3]
    ]
    return protagonist, world


def template_prompt(novel: Novel) -> tuple[str, str]:
    genre = GENRE_EN.get(novel.genre, novel.genre or "fiction")
    prompt = (
        f"book cover illustration, {genre}, a lone protagonist seen from behind, "
        "dramatic lighting, cinematic composition, empty space in the lower third, "
        "highly detailed digital painting, portrait orientation"
    )
    negative = "text, letters, title, logo, watermark, signature, blurry, deformed hands"
    return prompt, negative


def build_image_prompt(
    session: Session, novel: Novel, provider: LLMProvider | None, settings: Settings
) -> tuple[str, str, bool]:
    """(프롬프트, 제외 프롬프트, LLM을 썼는가)."""
    if provider is None or not provider.available:
        return (*template_prompt(novel), False)
    protagonist, world = _cover_inputs(session, novel)
    user = build_cover_prompt(
        title=novel.title,
        genre=novel.genre,
        logline=novel.logline,
        mood=novel.mood,
        protagonist=protagonist,
        world=world,
    )
    try:
        out, _ = request_structured(
            provider,
            [Message("system", COVER_SYSTEM), Message("user", user)],
            CoverPromptOut,
            retries=settings.llm_structured_retries,
            temperature=settings.llm_structured_temperature,
            max_tokens=800,
        )
    except StructuredOutputError:
        return (*template_prompt(novel), False)
    negative = out.negative_prompt or template_prompt(novel)[1]
    if "text" not in negative.lower():
        negative = f"text, letters, watermark, {negative}"
    return out.prompt, negative, True


# ---------------------------------------------------------------------------
# 그리기
# ---------------------------------------------------------------------------
def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except ImportError as exc:
        raise MissingDependencyError(
            "표지를 만들려면 Pillow가 필요합니다: pip install 'novel-factory[publish]'"
        ) from exc
    return Image, ImageDraw, ImageFont, ImageOps


def find_font(settings: Settings) -> Path | None:
    if settings.cover_font:
        path = Path(settings.cover_font)
        return path if path.is_file() else None
    return next((Path(p) for p in FONT_CANDIDATES if Path(p).is_file()), None)


def _load_font(path: Path | None, size: int):
    _, _, ImageFont, _ = _pil()
    if path is None:
        return ImageFont.load_default(size=size)
    return ImageFont.truetype(str(path), size=size)


def wrap_title(title: str, font, max_width: float) -> list[str]:
    """띄어쓰기로 줄을 나누고, 한 낱말이 너무 길면 글자 단위로 자른다."""
    lines: list[str] = []
    current = ""
    for word in title.split():
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        for ch in word:
            if font.getlength(current + ch) > max_width and current:
                lines.append(current)
                current = ""
            current += ch
    if current:
        lines.append(current)
    return lines


def _gradient(size: tuple[int, int], top, bottom) -> PILImage.Image:
    Image, _, _, _ = _pil()
    w, h = size
    column = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        column.putpixel(
            (0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom, strict=True))
        )
    return column.resize((w, h))


def compose_cover(
    art: bytes | None,
    *,
    title: str,
    author: str,
    genre: str,
    seed: str,
    size: tuple[int, int],
    font_path: Path | None,
) -> PILImage.Image:
    Image, ImageDraw, _, ImageOps = _pil()
    w, h = size
    if art is not None:
        picture = Image.open(io.BytesIO(art)).convert("RGB")
        canvas = ImageOps.fit(picture, (w, h), method=Image.Resampling.LANCZOS)
    else:
        idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(PALETTES)
        canvas = _gradient((w, h), *PALETTES[idx])
        # 장식: 위쪽에 얇은 원 두 개. 그림이 없을 때 표지가 너무 비어 보이지 않게.
        # RGB 그림이라 투명도가 없으므로 바탕색에 흰색을 섞은 색으로 그린다.
        top = PALETTES[idx][0]
        deco = ImageDraw.Draw(canvas)
        for r, mix in ((int(w * 0.26), 0.35), (int(w * 0.19), 0.22)):
            cx, cy = w // 2, int(h * 0.3)
            color = tuple(int(c + (255 - c) * mix) for c in top)
            deco.ellipse(
                (cx - r, cy - r, cx + r, cy + r), outline=color, width=max(2, w // 300)
            )

    # 아래쪽을 어둡게 깔아 글자가 읽히게 한다.
    shade = Image.new("L", (1, h))
    for y in range(h):
        t = max(0.0, (y / h - 0.55) / 0.45)
        shade.putpixel((0, y), int(210 * t))
    overlay = Image.new("RGB", (w, h), (0, 0, 0))
    canvas = Image.composite(overlay, canvas, shade.resize((w, h)))

    draw = ImageDraw.Draw(canvas)
    margin = int(w * 0.08)
    max_width = w - 2 * margin
    size_px = int(w * 0.12)
    while True:
        font = _load_font(font_path, size_px)
        lines = wrap_title(title, font, max_width)
        if len(lines) <= 3 or size_px <= int(w * 0.05):
            break
        size_px = int(size_px * 0.88)
    line_h = int(size_px * 1.25)
    small = _load_font(font_path, max(int(w * 0.035), 12))
    y = int(h * 0.92) - line_h * len(lines) - int(w * 0.06)
    if genre:
        draw.text((margin, y - int(w * 0.06)), genre, font=small, fill=(230, 230, 230))
    for line in lines:
        draw.text(
            (margin, y),
            line,
            font=font,
            fill=(255, 255, 255),
            stroke_width=max(1, size_px // 30),
            stroke_fill=(0, 0, 0),
        )
        y += line_h
    if author:
        draw.text((margin, y + int(w * 0.015)), author, font=small, fill=(235, 235, 235))
    return canvas


# ---------------------------------------------------------------------------
# 전체
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class CoverResult:
    cover: Path
    thumbnail: Path
    backend: str  # openai / a1111 / text
    prompt: str
    negative_prompt: str
    used_llm: bool
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "cover": str(self.cover),
            "thumbnail": str(self.thumbnail),
            "backend": self.backend,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "used_llm": self.used_llm,
            "warnings": self.warnings,
        }


def cover_dir(novel: Novel, settings: Settings | None = None) -> Path:
    cfg = settings or get_settings()
    return cfg.novels_dir / novel.slug / "cover"


def make_cover(
    session: Session,
    novel: Novel,
    provider: LLMProvider | None,
    *,
    settings: Settings | None = None,
    image_client: ImageClient | None = None,
    use_image: bool = True,
) -> CoverResult:
    cfg = settings or get_settings()
    _pil()  # Pillow가 없으면 여기서 바로 알린다
    warnings: list[str] = []
    prompt, negative, used_llm = build_image_prompt(session, novel, provider, cfg)

    art: bytes | None = None
    backend = "text"
    client = (
        image_client
        if image_client is not None
        else (get_image_client(cfg) if use_image else None)
    )
    if client is not None:
        try:
            art = client.generate(prompt, negative, cfg.image_width, cfg.image_height)
            backend = cfg.image_api if image_client is None else type(client).__name__
        except (LLMError, httpx.HTTPError, ValueError) as exc:
            warnings.append(f"이미지 생성 실패, 글자 표지로 만들었습니다: {exc}")
        finally:
            if image_client is None:
                client.close()  # type: ignore[attr-defined]

    font = find_font(cfg)
    if font is None:
        warnings.append(
            "한글 글꼴을 찾지 못했습니다. NF_COVER_FONT로 글꼴 파일을 지정하세요 "
            "(기본 글꼴은 한글을 못 그린다)."
        )
    info = get_info(novel)
    try:
        image = compose_cover(
            art,
            title=novel.title,
            author=info.author,
            genre=novel.genre,
            seed=novel.slug,
            size=(cfg.cover_width, cfg.cover_height),
            font_path=font,
        )
    except OSError as exc:  # 받은 그림이 이미지가 아닐 때
        warnings.append(f"받은 그림을 열 수 없어 글자 표지로 만들었습니다: {exc}")
        backend = "text"
        image = compose_cover(
            None,
            title=novel.title,
            author=info.author,
            genre=novel.genre,
            seed=novel.slug,
            size=(cfg.cover_width, cfg.cover_height),
            font_path=font,
        )

    folder = cover_dir(novel, cfg)
    folder.mkdir(parents=True, exist_ok=True)
    cover_path = folder / "cover.jpg"
    thumb_path = folder / "thumbnail.jpg"
    image.save(cover_path, "JPEG", quality=92)
    Image, _, _, _ = _pil()
    thumb = image.resize(
        (cfg.thumbnail_width, cfg.thumbnail_height), Image.Resampling.LANCZOS
    )
    thumb.save(thumb_path, "JPEG", quality=88)
    result = CoverResult(
        cover_path, thumb_path, backend, prompt, negative, used_llm, warnings
    )
    (folder / "cover.json").write_text(
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
