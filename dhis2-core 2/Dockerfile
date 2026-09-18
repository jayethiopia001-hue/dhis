ARG DHIS2_IMAGE=dhis2/core-dev:latest
FROM ${DHIS2_IMAGE}
USER root
RUN mkdir -p /opt/dhis2/files/apps && chown -R 65534:65534 /opt/dhis2/files
USER 65534
# Keep deployment-specific connection settings in the image. The database
# password must still be supplied at runtime so it is not stored in image
# layers or source control.
ENV DB_HOSTNAME=34.35.110.117 \
    DB_PORT=5432 \
    DB_NAME=dhis2 \
    DB_USERNAME=dhis2_app
# DHIS2 runs as UID 65534 and discovers installed apps under DHIS2_HOME/files/apps.
COPY --chown=65534:65534 dhis2-ai-backend/dhis2-app/ /opt/dhis2/files/apps/nextgen-planning/
COPY --chown=65534:65534 docker/dhis.conf /opt/dhis2/dhis.conf
COPY --chown=65534:65534 docker/start-dhis.sh /opt/dhis2/start-dhis.sh
RUN chmod 0555 /opt/dhis2/start-dhis.sh

EXPOSE 8080

ENTRYPOINT ["/opt/dhis2/start-dhis.sh"]
CMD ["catalina.sh", "run"]