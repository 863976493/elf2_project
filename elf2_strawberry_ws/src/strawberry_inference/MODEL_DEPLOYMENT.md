# Model Deployment

Copy the local model directory to the ELF2 board before launching inference:

```bash
mkdir -p /root/strawberry_models
```

Expected board paths:

```text
/root/strawberry_models/Disease/strawberry_yolov8n.rknn
/root/strawberry_models/Disease/strawberry.yaml
/root/strawberry_models/Maturity/best_fp16.rknn
```

These paths are configured in:

```text
strawberry_inference/config/inference_params.yaml
```

If the board uses a different model location, update the YAML file instead of editing code.
