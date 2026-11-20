import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count > 1 else {
    FileHandle.standardError.write("usage: vision_ocr <image> [min_confidence]\n".data(using: .utf8)!)
    exit(2)
}
let path = args[1]
let minConf = args.count > 2 ? (Float(args[2]) ?? 0.3) : 0.3
// 第三参数可指定语言，例如 "en-US" 或 "zh-Hans,en-US"
let langs: [String] = args.count > 3
    ? args[3].split(separator: ",").map { String($0) }
    : ["zh-Hans", "en-US"]

guard let img = NSImage(contentsOfFile: path),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot load image\n".data(using: .utf8)!)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = langs
// 保留小字号截图文本；此处设置识别文字最小高度，不对图片做缩放。
request.minimumTextHeight = 0.008
let handler = VNImageRequestHandler(cgImage: cg, options: [:])

struct Line {
    let text: String
    let rect: CGRect
}

var lines: [Line] = []
do {
    try handler.perform([request])
    for obs in request.results ?? [] {
        guard let top = obs.topCandidates(1).first else { continue }
        if top.confidence < minConf { continue }
        let t = top.string.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { continue }
        lines.append(Line(text: t, rect: obs.boundingBox))
    }
} catch {
    FileHandle.standardError.write("ocr failed: \(error)\n".data(using: .utf8)!)
    exit(1)
}

// Vision 坐标原点在左下：y 越大越靠上。先按行分组，行内按 x 从左到右。
lines.sort { a, b in
    let ay = a.rect.midY
    let by = b.rect.midY
    if abs(ay - by) > 0.015 { return ay > by }
    return a.rect.minX < b.rect.minX
}

var out: [String] = []
var cur: String? = nil
var curY: CGFloat = -1

for l in lines {
    if cur == nil {
        cur = l.text
        curY = l.rect.midY
    } else if abs(l.rect.midY - curY) <= 0.015 {
        cur! += " " + l.text
    } else {
        out.append(cur!)
        cur = l.text
        curY = l.rect.midY
    }
}
if let c = cur { out.append(c) }

print(out.joined(separator: "\n"))
