FROM grafana/grafana:latest

COPY grafana/provisioning /etc/grafana/provisioning
COPY grafana/dashboards   /etc/grafana/dashboards

# Anonymous viewers see dashboards with no login.
# Admin password comes from Railway env var at runtime, not baked into the image.
ENV GF_AUTH_ANONYMOUS_ENABLED=true
ENV GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
ENV GF_AUTH_DISABLE_LOGIN_FORM=false

EXPOSE 3000