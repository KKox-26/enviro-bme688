from breakout_bme68x import BreakoutBME68X
from ucollections import OrderedDict
from phew import logging

def get_readings(i2c, address):
    bme688 = BreakoutBME68X(i2c)
    bme688_data = bme688.read()

    readings = OrderedDict({
        "temperature": round(bme688_data[0], 2),
        "humidity": round(bme688_data[2], 2),
        "pressure": round(bme688_data[1] / 100.0, 2),
        "gas_resistance": round(bme688_data[3], 2)
    })
    
    for reading in readings:
        name_and_value = reading + " : " + str(readings[reading])
        logging.info(f"  - {name_and_value}")    

    return readings
