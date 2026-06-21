import os
import warnings
# Disable MKLDNN/oneDNN to bypass the PIR conversion error
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"

_ocr = None


def _get_ocr():
    """Lazy-init PaddleOCR to avoid heavy import cost."""
    global _ocr
    if _ocr is None:
        from paddleocr import PaddleOCR
        # Suppress "No ccache found" warning
        warnings.filterwarnings('ignore', message='.*ccache.*')
        # Suppress PaddleOCR model-creation noise
        import logging as _logging
        _logging.getLogger('paddlex').setLevel(_logging.WARNING)
        _logging.getLogger('paddle').setLevel(_logging.WARNING)
        _ocr = PaddleOCR(
            use_textline_orientation=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            lang='ch',
            enable_mkldnn=False
        )
    return _ocr


def ocr_screenshot2file(screenshot_path: str, output_path: str) -> None:
    """Run PaddleOCR on a screenshot and write sorted text lines to output_path."""
    ocr = _get_ocr()
    result = list(ocr.predict(screenshot_path))

    # Extract all text and box pairs
    text_box_pairs = []
    for item in result:
        rec_texts = item.get('rec_texts', [])
        rec_boxes = item.get('rec_boxes', [])
        for text, box in zip(rec_texts, rec_boxes):
            # box is [xmin, ymin, xmax, ymax]
            text_box_pairs.append((text, box))

    # Sort text boxes top-to-bottom, left-to-right using line grouping
    sorted_pairs = []
    if text_box_pairs:
        # Sort by ymin first
        sorted_by_y = sorted(text_box_pairs, key=lambda x: x[1][1])
        current_line = [sorted_by_y[0]]

        for item in sorted_by_y[1:]:
            ref_box = current_line[-1][1]
            ref_ymin, ref_ymax = ref_box[1], ref_box[3]
            box = item[1]
            item_ymin, item_ymax = box[1], box[3]

            # Calculate vertical overlap
            overlap_min = max(ref_ymin, item_ymin)
            overlap_max = min(ref_ymax, item_ymax)
            overlap_height = overlap_max - overlap_min

            ref_height = ref_ymax - ref_ymin
            item_height = item_ymax - item_ymin
            min_height = min(ref_height, item_height)

            # If they overlap vertically by more than 30% of the smaller height,
            # they belong to the same line
            if min_height > 0 and (overlap_height / min_height) > 0.3:
                current_line.append(item)
            else:
                # Sort current line left-to-right (by xmin)
                current_line.sort(key=lambda x: x[1][0])
                sorted_pairs.extend(current_line)
                current_line = [item]

        if current_line:
            current_line.sort(key=lambda x: x[1][0])
            sorted_pairs.extend(current_line)

    # Save and print sorted text
    with open(output_path, 'w', encoding='utf-8') as f:
        for text, _ in sorted_pairs:
            f.write(text + '\n')
            print(text)


if __name__ == '__main__':
    # Run OCR on your screenshot
    os.system("adb exec-out screencap -p > /tmp/screenshot.png")
    ocr_screenshot2file('/tmp/screenshot.png', '/tmp/screenshot.txt')
