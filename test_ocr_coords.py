import sys
import Quartz
import Vision
from AppKit import NSImage
import subprocess
import time
import pyautogui

def take_screenshot(filename="/tmp/test_ocr_coords.png"):
    subprocess.run(["screencapture", "-x", "-C", filename], check=True)
    return filename

def perform_ocr_with_coords(image_path):
    ns_image = NSImage.alloc().initWithContentsOfFile_(image_path)
    if not ns_image:
        print("Failed to load image")
        return []

    cg_image = ns_image.CGImageForProposedRect_context_hints_(None, None, None)[0]

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    
    success, error = handler.performRequests_error_([request], None)
    if not success:
        print(f"Error: {error}")
        return []

    results = []
    screen_w, screen_h = pyautogui.size()
    
    for observation in request.results():
        candidate = observation.topCandidates_(1).firstObject()
        if candidate:
            text = candidate.string()
            bbox = observation.boundingBox()
            
            # macOS Vision origin is bottom-left
            # pyautogui origin is top-left
            center_x = (bbox.origin.x + bbox.size.width / 2.0) * screen_w
            center_y = (1.0 - (bbox.origin.y + bbox.size.height / 2.0)) * screen_h
            
            results.append({
                "text": text,
                "x": center_x,
                "y": center_y
            })
            
    return results

if __name__ == "__main__":
    print("Taking screenshot...")
    path = take_screenshot()
    print("Scanning text...")
    results = perform_ocr_with_coords(path)
    
    for r in results:
        if "Accept" in r["text"]:
            print(f"Match: '{r['text']}' at point ({r['x']:.1f}, {r['y']:.1f})")
