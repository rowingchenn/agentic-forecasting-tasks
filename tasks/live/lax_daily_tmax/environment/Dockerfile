FROM ghcr.io/agentslastexam/container-ubuntu22-base:0.1.1

RUN python3 -m pip install --no-cache-dir \
        pandas==2.2.3 numpy==2.1.3 scipy==1.14.1 statsmodels==0.14.4 \
        scikit-learn==1.5.2 pyarrow==18.1.0 \
    && install -d -o user -g user /home/user/output
