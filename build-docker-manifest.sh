# make sure that local images are the most current ones
docker pull oliverrahner/beenera-mqtt-bridge:latest-amd64
docker pull oliverrahner/beenera-mqtt-bridge:latest-armv7

# update manifest
docker manifest create oliverrahner/beenera-mqtt-bridge:latest oliverrahner/beenera-mqtt-bridge:latest-amd64 oliverrahner/beenera-mqtt-bridge:latest-armv7
docker manifest push oliverrahner/beenera-mqtt-bridge:latest

