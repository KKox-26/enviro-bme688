import _thread
from time import ticks_ms, ticks_diff, time
from machine import Pin
import _thread
from math import pi
import enviro
from enviro.boards import weather
from phew import logging

class Multicore_Weather:
  def __init__(self) -> None:
    self.mww = Multicore_Weather_Wind()

  def init_multicore_poll_loop(self) -> None:
    # enviro.connect_to_wifi() existing enviro NTP code connects to wifi unconditionally
    enviro.pulse_activity_led()
    enviro.sync_clock_from_ntp()
    self.mww.init_wind_poll_thread()
    enviro.stop_activity_led()

    while True:
      self.poll_rain_pin()

      if self.mww.check_pending_wind_data_length() > 0:
        enviro.activity_led(100) # Pulse crashes execution
        all_data = self.collect_all_data()
        # Bouncing to disk maintains compatibility with original enviro code - no need to rewrite upload logic
        enviro.cache_upload(all_data)
        enviro.upload_readings()
        enviro.activity_led(0)

  def poll_rain_pin(self) -> None:
    weather.check_trigger()
  
  def collect_all_data(self) -> dict:
    sensors_reading = enviro.get_sensor_readings()
    wind_data = self.mww.get_pending_data()[-1]
    self.mww.clear_pending_data()
    all_data = sensors_reading | wind_data
    return all_data

class Multicore_Weather_Wind:
  def __init__(self) -> None:
    self.pending_wind_data_lock = _thread.allocate_lock()
    self.samples_lock = _thread.allocate_lock()
    self.wind_speed_pin = Pin(9, Pin.IN, Pin.PULL_UP)
    self.monitoring_window_s = 60
    self.sample_hz = 4
    self.sample_ms = 1000 / self.sample_hz
    self.samples = [[]] * (self.monitoring_window_s * self.sample_hz)
    self.samples_max_list_id = len(self.samples) - 1
    self.WIND_CM_RADIUS = 7.0
    self.WIND_FACTOR = 0.0218
    self.pending_wind_data = []
    self.debug = False
    self.processing_overhead_poll_count = 0
    self.last_loop_overhead_ms = 0
    self.remaining_loop_overhead_ms = 0
  
  def init_wind_poll_thread(self) -> None:
    self.wind_poll_thread = _thread.start_new_thread(self.constant_poll_wind_speed, ())

  def discard_overhead_compensation_poll(self) -> None:		
    if self.remaining_loop_overhead_ms >= self.sample_ms:
      self.remaining_loop_overhead_ms -= self.sample_ms
    else:
      start = ticks_ms()
      while ticks_diff(ticks_ms(), start) <= (self.sample_ms - self.last_loop_overhead_ms):
        pass
      self.remaining_loop_overhead_ms = 0
    
    self.processing_overhead_poll_count += 1
  
  def sample_wind_poll(self) -> list:
    previous_wind_pin_state = self.wind_speed_pin.value()
    ticks = []
    start = ticks_ms()
    while ticks_diff(ticks_ms(), start) <= self.sample_ms:
      current_wind_pin_state = self.wind_speed_pin.value()
      if current_wind_pin_state != previous_wind_pin_state:
        ticks.append(ticks_ms())
        previous_wind_pin_state = current_wind_pin_state
    
    return ticks
  
  def record_sample_datapoint(self, sample_id) -> None:
    ticks = self.sample_wind_poll()
    with self.samples_lock:
      self.samples[sample_id] = ticks

    if self.debug:
      print("sample {} has value: {}".format(sample_id, self.samples[sample_id]))
    
  def append_pending_wind_data(self, wind_data) -> None:
    with self.pending_wind_data_lock:
      self.pending_wind_data.append(wind_data)
  
  def calculate_processing_overhead(self) -> None:
    time_now_ms = ticks_ms()
    processing_time_ms = time_now_ms - self.previous_loop_time_ms
    self.previous_loop_time_ms = time_now_ms
    self.last_loop_overhead_ms = processing_time_ms - ((self.monitoring_window_s * 1000) - self.last_loop_overhead_ms)
    self.remaining_loop_overhead_ms = self.last_loop_overhead_ms
    self.processing_overhead_poll_count = 0
    if self.debug:
      print("Processing overhead: {}".format(self.last_loop_overhead_ms))
  
  def constant_poll_wind_speed(self) -> None:
    self.previous_loop_time_ms = ticks_ms()
    sample_id = 0
    
    while (True):
      if self.remaining_loop_overhead_ms > 0:
        self.discard_overhead_compensation_poll()
      else:
        self.record_sample_datapoint(sample_id)
      
      if sample_id < self.samples_max_list_id:
        sample_id += 1
      else:
        sample_id = 0
        self.append_pending_wind_data(self.process_wind_data())
        self.calculate_processing_overhead()
        
  
  def calculate_wind_speed_m_s(self, average_tick_ms: float) -> float:
    if average_tick_ms == 0:
      wind_m_s = 0
    else:
      rotation_hz = (1000 / average_tick_ms) / 2
      circumference = self.WIND_CM_RADIUS * 2.0 * pi
      wind_m_s = rotation_hz * circumference * self.WIND_FACTOR
    
    return wind_m_s

  def convert_sample_list_ticks_to_average_ms(self, samples) -> list:
    sample_average_tick_ms = []
    for sample in samples:
      if len(sample) > 1:
        average_tick_ms = (ticks_diff(sample[-1], sample[0])) / (len(sample) - 1)
        sample_average_tick_ms.append(average_tick_ms)
      else:
        sample_average_tick_ms.append(0)
    
    return sample_average_tick_ms
  
  def convert_sample_list_ms_to_speed_m_s(self, list_of_sample_average_ticks_in_ms) -> list:
    sample_speeds_in_m_s = []
    for sample in list_of_sample_average_ticks_in_ms:
      if sample == 0:
        sample_speeds_in_m_s.append(0)
      else:
        sample_speeds_in_m_s.append(self.calculate_wind_speed_m_s(sample))
    
    return sample_speeds_in_m_s

  def calculate_average_wind(self, list_of_sample_speeds_in_m_s) -> float:
    average_wind_speed = sum(list_of_sample_speeds_in_m_s) / len(list_of_sample_speeds_in_m_s)
    
    return average_wind_speed

  def determine_gust_wind(self, list_of_sample_speeds_in_m_s) -> float:
    gust_wind_speed = 0
    for sample in list_of_sample_speeds_in_m_s:
        if sample > gust_wind_speed:
          gust_wind_speed = sample

    return gust_wind_speed

  def cache_samples(self) -> None:
    with self.samples_lock:
      self.cached_samples = self.samples
  
  def remove_processing_overhead_data_polls(self, samples) -> list:
    adjusted_samples = self.cached_samples[0 + self.processing_overhead_poll_count : -1]
    return adjusted_samples
  
  def process_wind_data(self) -> dict[str, float]:
    self.cache_samples()
    samples = self.remove_processing_overhead_data_polls(self.cached_samples)

    list_of_sample_average_ticks_in_ms = self.convert_sample_list_ticks_to_average_ms(samples)
    logging.info(f"> list of sample average ticks in ms: {list_of_sample_average_ticks_in_ms}")
    list_of_sample_speeds_in_m_s = self.convert_sample_list_ms_to_speed_m_s(list_of_sample_average_ticks_in_ms)
    logging.info(f"> list of sample wind speeds in m/s: {list_of_sample_speeds_in_m_s}")
    
    gust_wind = self.determine_gust_wind(list_of_sample_speeds_in_m_s)
    logging.info(f"> gust wind speed is: {gust_wind}")
    average_wind = self.calculate_average_wind(list_of_sample_speeds_in_m_s)    
    logging.info(f"> average wind speed is: {average_wind}")

    return {"timestamp": time(), "avg_wind_speed": average_wind, "gust_wind_speed": gust_wind}
  
  def get_pending_data(self) -> list:
    """
    Returns a list of dictionaries {"timestamp" : unixtimestamp, "avg_wind_speed" : float, "gust_wind_speed" : float}
    """
    with self.pending_wind_data_lock:
      pending_data = self.pending_wind_data
    
    return pending_data
  
  def clear_pending_data(self) -> None:
    with self.pending_wind_data_lock:
      self.pending_wind_data = []
  
  def check_pending_wind_data_length(self) -> int:
    with self.pending_wind_data_lock:
      length = len(self.pending_wind_data)

    return length
