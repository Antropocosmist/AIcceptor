import sys
import Quartz
import Vision
from AppKit import NSImage
import subprocess
import time

def take_screenshot(filename="/tmp/test_ocr.png"):
    subprocess.run(["screencapture", "-x", "-C", filename], check=True)
    return filename

def perform_ocr(image_path):
    # Load image using NSImage
    ns_image = NSImage.alloc().initWithContentsOfFile_(image_path)
    if not ns_image:
        print("Failed to load image")
        return []

    # Get CGImage
    cg_image = ns_image.CGImageForProposedRect_context_hints_(None, None, None)[0]

    # Create OCR Request
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)

    # Create handler
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    
    # Perform request
    success, error = handler.performRequests_error_([request], None)
    if not success:
        print(f"Error: {error}")
        return []

    results = []
    for observation in request.results():
        # Get top candidate
        candidate = observation.topCandidates_(1).firstObject()
        if candidate:
            results.append(candidate.string())
            
    return results

if __name__ == "__main__":
    print("Taking screenshot...")
    path = take_screenshot()
    print("Scanning text...")
    start_time = time.time()
    texts = perform_ocr(path)
    end_time = time.time()
    
    found_accept = any("Accept" in text for text in texts)
    
    print(f"Completed in {end_time - start_time:.2f} seconds.")
    print(f"Total phrases found: {len(texts)}")
    print(f"'Accept' found: {found_accept}")
    
    for text in texts:
        if "Accept" in text:
            print(f"Found match: {text}")
