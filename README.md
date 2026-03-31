# HFDownloader - Hugging Face Model Downloader

HFDownloader downloads a model repository from the [Hugging Face Model Hub](https://huggingface.co/models) into a local folder.

This project now supports both:

- a no-terminal Windows GUI
- a command-line interface
- a small Python API

# No-Terminal Usage on Windows

If you do not want to use a terminal, launch the app by double-clicking one of these files in the project folder:

- `HFDownloader_GUI.pyw`
- `HFDownloader_GUI.vbs`

Then:

1. Enter the Hugging Face model id, such as `cardiffnlp/twitter-roberta-base-sentiment`
2. Choose the base folder where you want the files saved
3. (Optional) Add a Hugging Face token only if the model is gated or private
4. Click `Download`

The app creates a visible model folder inside the base folder, opens it while downloading, and shows saved files in the activity panel.

# Command-Line Usage

If you do want the script interface, use:

```powershell
python src\hfdownloader\hf_downloader.py cardiffnlp/twitter-roberta-base-sentiment sentiment_model_path
```

Optional flags:

```powershell
python src\hfdownloader\hf_downloader.py meta-llama/Llama-3.2-1B-Instruct llama_files --token YOUR_TOKEN --revision main
```

# Installation

Install from PyPI:

```powershell
pip install hfdownloader
```

Or install this local checkout:

```powershell
pip install -e .
```

If you install it from source, you will also get launchers:

- `hfdownloader`
- `hfdownloader-gui`

# Python Usage

To download a model from Python:

```python
from hfdownloader import download_model

download_model(
    "cardiffnlp/twitter-roberta-base-sentiment",
    "HuggingFaceModelPath",
)
```

If you already have a tokenizer and model loaded, you can still save them with:

```python
from hfdownloader import save_model

save_model("HuggingFaceModelPath", tokenizer, model)
```

# Requirements

```powershell
pip install huggingface_hub
```

For gated or private models, you may also need a Hugging Face access token.

# Notes

`HFDownloader_GUI.pyw` is intended for double-click usage on Windows without opening a terminal window.
