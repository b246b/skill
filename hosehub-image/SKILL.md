---
name: hosehub-image
description: Generate new images or edit existing images through the HoseHub gpt-image-2 API. Use when the user asks to create or transform raster images with HoseHub; do not use for SVG, HTML/CSS, or local-only image manipulation.
---

# HoseHub Image

Use `scripts/hosehub_image.py` for both generation and editing. The client uses
the fixed API endpoints under `https://ai.qhose.net/v1/images` and reads the
credential only from `HOSEHUBAPI_KEY`.

## Before Calling the API

- Confirm the prompt, operation, input images, output directory, and any size or
  count the user specified.
- Treat every real request as an external write that may incur charges. Obtain
  explicit approval immediately before the first real request unless the user
  already clearly authorized generating or editing that image in the current
  turn.
- Tell the user that prompts and edit inputs are uploaded to HoseHub when that
  is not already clear from context.
- Never place the API key in a command, source file, prompt, log, or response.
  If `HOSEHUBAPI_KEY` is absent, stop and explain how to set it locally.
- Do not retry a failed paid request automatically. Report the failure and ask
  before another request unless the failure proves no request reached the API.

## Generate

Run:

```powershell
python scripts/hosehub_image.py generate `
  --prompt "A cyberpunk cat, cinematic lighting" `
  --size "1024x1024" `
  --output-dir "C:\path\to\outputs"
```

Pass `--n` only when the user wants multiple images. The default model is
`gpt-image-2`; pass `--model` only when the user requests another supported
model.

## Edit

Run:

```powershell
python scripts/hosehub_image.py edit `
  --image "C:\path\to\input.png" `
  --prompt "add neon signs in background" `
  --output-dir "C:\path\to\outputs"
```

Repeat `--image` for multiple edit inputs. Do not overwrite source images; the
script always creates new output files.

## Deliver Results

The script prints one absolute saved path per output. Check that every path
exists and has non-zero size before claiming success. In Codex Desktop, show
the resulting image with an absolute-path Markdown image link when useful.
