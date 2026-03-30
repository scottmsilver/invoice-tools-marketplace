import io
import logging
from typing import Tuple

import fitz  # PyMuPDF - used for animated GIFs
import img2pdf
from PIL import Image

logger = logging.getLogger(__name__)


class ImageConverter:
    """Convert image files (GIF, PNG, JPG, JPEG) to PDF format."""

    SUPPORTED_FORMATS = {".gif", ".png", ".jpg", ".jpeg"}

    @staticmethod
    def is_supported_image(filename: str) -> bool:
        return any(filename.lower().endswith(ext) for ext in ImageConverter.SUPPORTED_FORMATS)

    @staticmethod
    def convert_image_to_pdf(image_bytes: bytes, filename: str) -> Tuple[bytes, int]:
        """Convert an image file to a single-page PDF.
        Returns: (pdf_bytes, page_count)
        """
        try:
            image = Image.open(io.BytesIO(image_bytes))

            if filename.lower().endswith(".gif"):
                return ImageConverter._convert_gif_to_pdf(image)

            try:
                pdf_bytes = img2pdf.convert(image_bytes, nodate=True, engine=img2pdf.Engine.internal)
                return pdf_bytes, 1
            except img2pdf.PdfTooLargeError:
                max_size = (8192, 8192)
                image.thumbnail(max_size, Image.Resampling.LANCZOS)
                img_buffer = io.BytesIO()
                image.save(img_buffer, format="PNG")
                resized_bytes = img_buffer.getvalue()
                pdf_bytes = img2pdf.convert(resized_bytes, nodate=True, engine=img2pdf.Engine.internal)
                return pdf_bytes, 1

        except Exception as e:
            raise ValueError(f"Failed to convert image to PDF: {str(e)}")

    @staticmethod
    def _convert_gif_to_pdf(gif_image: Image.Image) -> Tuple[bytes, int]:
        pdf_document = fitz.open()
        frame_count = 0

        try:
            for frame_num in range(gif_image.n_frames):
                gif_image.seek(frame_num)
                frame = gif_image.convert("RGB")
                img_buffer = io.BytesIO()
                frame.save(img_buffer, format="PNG")
                img_bytes = img_buffer.getvalue()
                width_pt = frame.width * 72 / 96
                height_pt = frame.height * 72 / 96
                page = pdf_document.new_page(width=width_pt, height=height_pt)
                page.insert_image(page.rect, stream=img_bytes)
                frame_count += 1
        except EOFError:
            pass
        except AttributeError:
            frame = gif_image.convert("RGB")
            img_buffer = io.BytesIO()
            frame.save(img_buffer, format="PNG")
            img_bytes = img_buffer.getvalue()
            width_pt = frame.width * 72 / 96
            height_pt = frame.height * 72 / 96
            page = pdf_document.new_page(width=width_pt, height=height_pt)
            page.insert_image(page.rect, stream=img_bytes)
            frame_count = 1

        pdf_document.set_metadata(
            {
                "producer": "ImageConverter",
                "creator": "ImageConverter",
                "creationDate": "D:20000101000000Z00'00'",
                "modDate": "D:20000101000000Z00'00'",
            }
        )
        pdf_bytes = pdf_document.tobytes(deflate=True, clean=True, garbage=4, linear=False)
        pdf_document.close()
        return pdf_bytes, frame_count
