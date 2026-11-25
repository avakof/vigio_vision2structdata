#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
state="$PWD/.vt-state/paddle"
export UV_CACHE_DIR="$state/uv-cache"
export HF_HOME="$state/hf"
export PADDLE_PDX_CACHE_HOME="$state/paddlex"

fetch() {
    url=$1
    destination=$2
    expected_size=${3:-}
    if [ -f "$destination" ]; then
        actual_size=$(stat -f '%z' "$destination")
        if [ -z "$expected_size" ] || [ "$actual_size" = "$expected_size" ]; then
            return
        fi
    fi
    if [ -n "$expected_size" ]; then
        curl -fLsS --connect-timeout 15 -C - -o "$destination" "$url"
    else
        curl -fLsS --connect-timeout 15 -o "$destination.part" "$url"
        mv "$destination.part" "$destination"
    fi
}

case ${1:-} in
    packages)
        if [ ! -x "$state/env/bin/python" ]; then
            uv venv --python python3.12 "$state/env"
        fi
        uv pip install --python "$state/env/bin/python" \
            'paddlepaddle==3.2.1' 'paddleocr[doc-parser]==3.7.0' 'paddlex==3.7.0'
        ;;
    layout)
        base='https://huggingface.co/PaddlePaddle/PP-DocLayoutV3/resolve/241f8bdfc77a7c7bee915a5057aaee58c235a8d3'
        dir="$state/models/layout"
        mkdir -p "$dir"
        for name in inference.json inference.yml; do
            fetch "$base/$name" "$dir/$name"
        done
        fetch "$base/inference.pdiparams" "$dir/inference.pdiparams" 130806572
        ;;
    vlm)
        base='https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6/resolve/132c45f52c0a26ed5a6b2f6bd8e3985794cc27dd'
        dir="$state/models/vlm"
        mkdir -p "$dir"
        for name in added_tokens.json chat_template.jinja config.json \
            configuration_paddleocr_vl.py generation_config.json \
            image_processing_paddleocr_vl.py inference.yml LICENSE \
            modeling_paddleocr_vl.py preprocessor_config.json processing_paddleocr_vl.py \
            processor_config.json special_tokens_map.json tokenizer.json tokenizer.model \
            tokenizer_config.json; do
            fetch "$base/$name" "$dir/$name"
        done
        fetch "$base/model.safetensors" "$dir/model.safetensors" 1917255968
        ;;
    *)
        echo 'usage: paddle-gate1-provision.sh {packages|layout|vlm}' >&2
        exit 2
        ;;
esac
