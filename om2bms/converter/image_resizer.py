"""
Resizes BG to 640x480.
"""
import os

from PIL import Image, ImageOps


RESAMPLE_LANCZOS = Image.Resampling.LANCZOS if hasattr(
    Image, "Resampling") else Image.LANCZOS


def black_background_thumbnail(path_to_image, target_size=(640, 480)):
    """
    Resizes image to fit inside 640x480 without black borders when needed.
    """
    img = Image.open(path_to_image).convert("RGB")
    target_w, target_h = target_size
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    right = left + target_w
    bottom = top + target_h
    img = img.crop((left, top, right, bottom))
    img.save(path_to_image)
    img.close()


def build_banner_name(path_to_image: str) -> str:
    """
    Returns the companion banner filename for a background image.
    """
    return "BANNER_" + os.path.basename(path_to_image)


def save_banner_image(path_to_image: str, banner_size=(300, 80)) -> str:
    """
    Creates a 300x80 banner image beside the original background.
    The source image is center-cropped to match LR2-style banner proportions.
    """
    dir_name, file_name = os.path.split(path_to_image)
    banner_name = "BANNER_" + file_name
    banner_path = os.path.join(dir_name, banner_name)

    with Image.open(path_to_image) as image:
        source_image = image.convert("RGB")
        banner = ImageOps.fit(
            source_image,
            banner_size,
            method=RESAMPLE_LANCZOS,
            centering=(0.5, 0.5),
        )
        banner.save(banner_path)
        banner.close()
        source_image.close()
    return banner_path
