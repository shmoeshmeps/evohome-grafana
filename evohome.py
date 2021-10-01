from evohomeclient2 import EvohomeClient, AuthenticationError
from influxdb import InfluxDBClient
from requests.exceptions import ConnectionError
import time
import os
import requests

print("Starting")
username = os.environ['EH-USERNAME']
password = os.environ['EH-PASSWORD']

starttime = time.time()
if __name__ == "__main__":
    all_ok = True
    # Connect to Influxdb
    client = InfluxDBClient(host='influxdb', port=8086)
    print("Connected to InfluxDB")
    client.create_database("EH-TEMPS")
    print("Database EH-TEMPS exists")
    client.get_list_database()
    print("Initialising evohome API")
    try:
        eclient = EvohomeClient({username}, {password})
    except AuthenticationError as e:
        print("API overload error - will trsleeping 5 mins before container restart")
        time.sleep(300)
        print("Sleep slept")
        all_ok = False

    while all_ok:
        try:
            # Request evohome temperatures
            print("Collect and store evohome temperatures")
            eclient = EvohomeClient({username}, {password})
            temps_data = list(eclient.temperatures())
            # print(temps_data)
            for device in temps_data:
                # Hot Water has null strings for name and setpoint so manually add them
                if device['thermostat'] == "DOMESTIC_HOT_WATER":
                    device['setpoint'] = 55.0
                    device['name'] = "Hot Water"
                print([{"measurement":"Temperatures","fields":device}])
                client.write_points([{"measurement":"Temperatures","fields":device}], database='EH-TEMPS')

            # Collect and store OH temperatures
            print("Collect and store OH temperatures")
            if "OW" in os.environ:
                API_key = os.environ['OW-API-KEY']
                city_name = os.environ['OW-CITY']
                base_url = "http://api.openweathermap.org/data/2.5/weather?"
                Final_url = base_url + "q=" + city_name + "&appid=" + API_key + "&units=metric"
                weather_data = requests.get(Final_url).json()
                temp = weather_data['main']['temp']
                print([{"measurement":"ext-Temperatures","fields":{'ext-temp': temp}}])
                client.write_points([{"measurement":"ext-Temperatures","fields":{'ext-temp': temp}}], database='EH-TEMPS')

            # Inform Healthchecks.io
            if "HEALTHCHECKS-IO" in os.environ:
                healthchecks = os.environ['HEALTHCHECKS-IO']
                requests.get(healthchecks)

        except ConnectionError as e:
            print("No Database Connection")
        except AuthenticationError as e:
            print("API overload error - sleeping 5 mins before retry")
        time.sleep(300.0 - ((time.time() - starttime) % 300.0))
