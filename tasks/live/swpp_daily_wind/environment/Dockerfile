FROM ghcr.io/agentslastexam/container-ubuntu22-base:0.1.1

# Optional build-only CA for environments with an HTTPS inspection proxy.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python3 -m pip install --no-cache-dir \
        pandas==2.2.3 numpy==2.1.3 scipy==1.14.1 statsmodels==0.14.4 \
        scikit-learn==1.5.2 pyarrow==18.1.0 \
    && install -d -o user -g user /home/user/output
