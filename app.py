import os
import uuid
from pathlib import Path

from flask import Flask, abort, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "Uploaded_Files"
MODEL_PATH = BASE_DIR / "model" / "df_model.pt"

ALLOWED_PAGES = {
    "index.html",
    "app.html",
    "about.html",
    "contact.html",
    "dashboard.html",
    "detect.html",
    "detected.html",
    "faq.html",
    "login.html",
    "signup.html",
}

ALLOWED_VIDEO_EXTS = {"mp4", "mov", "avi", "mkv", "webm"}

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)


def _allowed_video(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_VIDEO_EXTS


def _run_deepfake_detection(video_path: Path):
    """Returns (label, confidence) where label is REAL or FAKE."""
    os.environ["KMP_DUPLICATE_LIB_OK"] = "True"

    import warnings

    warnings.filterwarnings("ignore")

    import cv2
    import face_recognition
    import numpy as np
    import torch
    from torch import nn
    from torch.utils.data import Dataset
    from torchvision import models, transforms

    class Model(nn.Module):
        def __init__(self, num_classes, latent_dim=2048, lstm_layers=1, hidden_dim=2048, bidirectional=False):
            super().__init__()
            model = models.resnext50_32x4d(weights="ResNeXt50_32X4D_Weights.IMAGENET1K_V1")
            self.model = nn.Sequential(*list(model.children())[:-2])
            self.lstm = nn.LSTM(latent_dim, hidden_dim, lstm_layers, bidirectional)
            self.dp = nn.Dropout(0.4)
            self.linear1 = nn.Linear(2048, num_classes)
            self.avgpool = nn.AdaptiveAvgPool2d(1)

        def forward(self, x):
            batch_size, seq_length, c, h, w = x.shape
            x = x.view(batch_size * seq_length, c, h, w)
            fmap = self.model(x)
            x = self.avgpool(fmap)
            x = x.view(batch_size, seq_length, 2048)
            x_lstm, _ = self.lstm(x, None)
            return fmap, self.dp(self.linear1(x_lstm[:, -1, :]))

    class ValidationDataset(Dataset):
        def __init__(self, video_names, sequence_length=20, transform=None):
            self.video_names = video_names
            self.transform = transform
            self.count = sequence_length

        def __len__(self):
            return len(self.video_names)

        def __getitem__(self, idx):
            current_video_path = self.video_names[idx]
            frames = []
            for frame in self.frame_extract(current_video_path):
                if frame is None or getattr(frame, "size", 0) == 0:
                    continue

                # OpenCV gives BGR; face_recognition expects 8-bit RGB.
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                try:
                    faces = face_recognition.face_locations(rgb_frame)
                except Exception:
                    faces = []

                if faces:
                    top, right, bottom, left = faces[0]
                    h, w = rgb_frame.shape[:2]
                    top = max(0, min(top, h))
                    bottom = max(0, min(bottom, h))
                    left = max(0, min(left, w))
                    right = max(0, min(right, w))
                    if bottom > top and right > left:
                        rgb_frame = rgb_frame[top:bottom, left:right, :]

                frames.append(self.transform(rgb_frame))
                if len(frames) == self.count:
                    break

            if not frames:
                raise RuntimeError("No readable video frames found.")

            while len(frames) < self.count:
                frames.append(frames[-1])

            stacked = torch.stack(frames[: self.count])
            return stacked.unsqueeze(0)

        @staticmethod
        def frame_extract(path):
            vid_obj = cv2.VideoCapture(str(path))
            success = True
            while success:
                success, image = vid_obj.read()
                if success:
                    yield image

    im_size = 112
    mean = [0.485, 0.456, 0.406]
    std = [0.229, 0.224, 0.225]

    transform_pipeline = transforms.Compose(
        [
            transforms.ToPILImage(),
            transforms.Resize((im_size, im_size)),
            transforms.ToTensor(),
            transforms.Normalize(mean, std),
        ]
    )

    dataset = ValidationDataset([str(video_path)], sequence_length=20, transform=transform_pipeline)
    model = Model(2)

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model file not found at {MODEL_PATH}")

    model.load_state_dict(torch.load(MODEL_PATH, map_location=torch.device("cpu")))
    model.eval()

    softmax = nn.Softmax(dim=1)
    _, logits = model(dataset[0])
    probs = softmax(logits)
    _, pred_idx = torch.max(probs, 1)
    confidence = float(probs[:, int(pred_idx.item())].item() * 100)

    label = "REAL" if int(pred_idx.item()) == 1 else "FAKE"
    return label, round(confidence, 2)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/Detect", methods=["GET", "POST"])
@app.route("/detect", methods=["GET", "POST"])
def detect_page():
    if request.method == "GET":
        return render_template("detect.html")

    uploaded = request.files.get("video")
    if not uploaded or not uploaded.filename:
        return render_template("detect.html", error="Please choose a video file first.")

    filename = secure_filename(uploaded.filename)
    if not _allowed_video(filename):
        return render_template(
            "detect.html",
            error="Unsupported file type. Upload one of: mp4, mov, avi, mkv, webm.",
        )

    temp_name = f"{uuid.uuid4().hex}_{filename}"
    saved_path = UPLOAD_FOLDER / temp_name
    uploaded.save(saved_path)

    try:
        output, confidence = _run_deepfake_detection(saved_path)
        data = {"output": output, "confidence": confidence}
        return render_template("detect.html", data=data)
    except Exception as exc:
        return render_template(
            "detect.html",
            error=f"Detection engine error: {exc}. Install backend ML dependencies and verify model path.",
        )
    finally:
        if saved_path.exists():
            saved_path.unlink()


@app.route("/styles.css")
def legacy_styles():
    return send_from_directory(app.static_folder, "styles.css")


@app.route("/script.js")
def legacy_script():
    return send_from_directory(app.static_folder, "script.js")


@app.route("/<path:page>")
def static_pages(page: str):
    if page in ALLOWED_PAGES:
        return render_template(page)
    abort(404)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
