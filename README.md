# beenera-mqtt-bridge

This project provides a "bridge" to receive data from the Beenera project (https://beenera.de/) and pass it on to another MQTT broker.

Via this way, one can easily use the provided information in home automation systems like OpenHAB, Home Assistant, FHEM, iobroker or all others that provide some means to access data via MQTT.

## Setup

### MQTT broker

If you do not currently have an MQTT broker available, you can simply install one.
The easiest is to install it on the same machine as your home automation system.

Example installation on Debian:
```shell
apt update
apt install -y mosquitto mosquitto-clients
```

> :warning: The default install allows anonymous and unencrypted traffic from all machines! This is, if at all, only suitable in your internal home network.

The broker should automatically start up on reboot. If that's not the case, a simple
```shell
systemctl enable mosquitto
```
should be enough.

### beenera-mqtt-bridge



### OpenHAB

To get the MQTT values into OpenHAB, create the following configuration:

#### Things

`/etc/openhab/things/beenera.things`:

```
// this line is only needed if you don't have an MQTT broker set up currently
// be sure to change the host if needed
Bridge mqtt:broker:broker [ host="localhost", secure=false, clientid="openhab"]

Thing mqtt:topic:power:<Meter ID> "Beenera <Meter ID>" (mqtt:broker:broker) {
// you can freely rename the channel names, just be sure to change the items accordingly
    Channels:
        Type number : CurrentPower            [stateTopic="power/<Meter ID>", transformationPattern="JSONPATH:$.items[0].values[?(@.obis=='1-0:16.7.0*255')].value"]
        Type number : PowerConsumptionMeter   [stateTopic="power/<Meter ID>", transformationPattern="JSONPATH:$.items[0].values[?(@.obis=='1-0:1.8.0*255')].value"]
        Type number : PowerDeliveryMeter      [stateTopic="power/<Meter ID>", transformationPattern="JSONPATH:$.items[0].values[?(@.obis=='1-0:2.8.0*255')].value"]
}
```

Be sure to replace `<Meter ID>` by your actual meter ID. You can easily look it up in the Beenera app, by examining the log output of the bridge or by plain looking at your meter 😉

The bridge will log a line like this:
```
First time publishing data to topic 'power/1EMH000....'
```
where 1EMH000... is your meter ID.

#### Items

You can either create the Items via the UI if this is what you usually do, or you can define them in a file:

`/etc/openhab/items/beenera.items`
```
Number pow_beenera_CurrentPower "Current Power [%.1f W]" { channel="mqtt:topic:power:<Meter ID>:CurrentPower" }
Number pow_beenera_DeliveryMeter "Meter Reading Delivery [%.1f Wh]" { channel="mqtt:topic:power:<Meter ID>:PowerDeliveryMeter" }
Number pow_beenera_ConsumptionMeter "Meter Reading Consumption [%.1f Wh]" { channel="mqtt:topic:power:<Meter ID>:PowerConsumptionMeter" }
```

Again, replace the `<Meter ID>` everywhere.
