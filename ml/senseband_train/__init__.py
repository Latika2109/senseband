"""Training pipeline: dataset download/convert, windows, LightGBM training, ONNX export, metrics, benchmark."""

from senseband_features import FEATURE_VERSION  # noqa: E402,F401  (training and edge share one definition)
