import sys
import Quartz
import Vision
from AppKit import NSImage
import subprocess
import time
import pyautogui

def take_screenshot(filename="/tmp/test_ocr_coords.png"):
    subprocess.run(["screencapture", "-x", "-m", "-C", filename], check=True)
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
    print(f"PyAutoGUI thinks screen is {screen_w}x{screen_h}")
    
    for observation in request.results():
        candidate = observation.topCandidates_(1).firstObject()
        if candidate:
            text = candidate.string()
            
            if "accept" in text.lower():
                bbox = observation.boundingBox()
                
                center_x = (bbox.origin.x + bbox.size.width / 2.0) * screen_w
                center_y = (1.0 - (bbox.origin.y + bbox.size.height / 2.0)) * screen_h
                
                results.append({
                    "text": text,
                    "x": center_x,
                    "y": center_y,
                    "bbox_x": bbox.origin.x,
                    "bbox_y": bbox.origin.y,
                    "bbox_w": bbox.size.width,
                    "bbox_h": bbox.size.height
                })
            
    return results

if __name__ == "__main__":
    print("Taking screenshot...")
    path = take_screenshot()
    print("Scanning text...")
    results = perform_ocr_with_coords(path)
    
    target = None
    for r in results:
        print(f"Match: '{r['text']}' at point ({r['x']:.1f}, {r['y']:.1f}) | NormBox: x={r['bbox_x']:.3f}, y={r['bbox_y']:.3f}, w={r['bbox_w']:.3f}, h={r['bbox_h']:.3f}")
        if "all" in r['text'].lower() and len(r['text']) < 30:
            target = r

    if not target and results:
        for r in results:
            if len(r['text']) < 30:
                target = r
                break

    if target:
        print(f"\\nMOVING MOUSE TO: {target['text']} at ({target['x']}, {target['y']})")
        pyautogui.moveTo(target['x'], target['y'], duration=2)
    else:
        print("No button found.")
