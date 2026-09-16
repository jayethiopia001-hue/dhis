ARG DHIS2_IMAGE=dhis2/core-dev:latest
FROM ${DHIS2_IMAGE}

# DHIS2 runs as UID 65534 and discovers installed apps under DHIS2_HOME/files/apps.
COPY --chown=65534:65534 dhis2-ai-backend/dhis2-app/ /opt/dhis2/files/apps/nextgen-planning/
COPY --chown=65534:65534 docker/dhis.conf /opt/dhis2/dhis.conf
COPY --chown=65534:65534 docker/start-dhis.sh /opt/dhis2/start-dhis.sh
RUN chmod 0555 /opt/dhis2/start-dhis.sh

EXPOSE 8080

ADD https://repo1.maven.org/maven2/com/google/cloud/sql/postgres-socket-factory/1.28.1/postgres-socket-factory-1.28.1-jar-with-dependencies.jar \
    /usr/local/tomcat/lib/

ENTRYPOINT ["/opt/dhis2/start-dhis.sh"]
CMD ["catalina.sh", "run"]