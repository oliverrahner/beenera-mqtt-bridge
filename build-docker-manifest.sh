# make sure that local images are the most current ones
docker pull docker.io/oliverrahner/beenera-mqtt-bridge:latest-amd64
docker pull docker.io/oliverrahner/beenera-mqtt-bridge:latest-armv7

# update manifest
docker manifest rm docker.io/oliverrahner/beenera-mqtt-bridge:latest
docker manifest create docker.io/oliverrahner/beenera-mqtt-bridge:latest docker.io/oliverrahner/beenera-mqtt-bridge:latest-amd64 docker.io/oliverrahner/beenera-mqtt-bridge:latest-armv7
docker manifest push docker.io/oliverrahner/beenera-mqtt-bridge:latest
