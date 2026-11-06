# grace/vllm — vLLM for the GX10 (GB10 / sm_121a, aarch64, CUDA 13)
#
# Check what exists with:
#   docker run --rm quay.io/skopeo/stable list-tags docker://vllm/vllm-openai | grep aarch64 | tail
ARG VLLM_TAG=v0.31.0-aarch64
FROM vllm/vllm-openai:${VLLM_TAG}

# Gemma 4 audio input needs the [audio] extras (librosa/soundfile) that the
# base image omits. 
RUN uv pip install --system librosa soundfile

# Let compose own the full command line.
ENTRYPOINT []
