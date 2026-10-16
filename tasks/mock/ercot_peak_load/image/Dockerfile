FROM ghcr.io/agentslastexam/container-ubuntu22-base:latest

RUN python3 -m pip install --no-cache-dir \
        pandas==2.2.3 numpy==2.1.3 scipy==1.14.1 statsmodels==0.14.4 \
        scikit-learn==1.5.2 pyarrow==18.1.0 \
    && install -d -o user -g user /home/user/output

# Hosted search runs at the model provider, outside the sandbox network, so a blocked
# network alone does not stop it. Managed settings take precedence over the agents' own
# configuration: Codex reads /etc/codex/managed_config.toml, Claude Code
# /etc/claude-code/managed-settings.json.
RUN mkdir -p /etc/codex /etc/claude-code \
    && printf 'web_search = "disabled"\n' > /etc/codex/managed_config.toml \
    && printf '{"permissions": {"deny": ["WebSearch", "WebFetch"]}}\n' \
        > /etc/claude-code/managed-settings.json
