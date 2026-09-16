How to start dhis2

DHIS2_IMAGE=dhis2/core:2.40.0.1 \                                            
DHIS2_DB_DUMP_URL=https://databases.dhis2.org/sierra-leone/2.39.1/dhis2-db-sierra-leone.sql.gz \
DB_USERNAME=dhis \
DB_PASSWORD=dhis \

-----------------------------------------------------------------
gcloud auth application-default login
gcloud auth application-default set-quota-project ethiopia-nextgen-planning
-----------------------------------------------------------------
How to start dhis2
cd /path/to/dhis2-core/dhis2-core/dhis2-core

Might need to 
export DB_USERNAME=dhis
export DB_PASSWORD=dhis
export DB_NAME=dhis

Then
docker compose up
This start FastAPI on http://localhost:8000/docs#/ -->> changed to http://localhost:8001/docs#/
And DHIS2 on http://localhost:8080/apps/dashboard#/

username: admin
password: district
-----------------------------------------------------------------


Start sql server
 docker exec -it dhis2-core-db-1 psql -U dhis -d dhis   