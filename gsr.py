import threading
import datetime
from config import processing_unit
import csv
import struct, serial
import math

class GSRStreaming(processing_unit):
    def __init__(self, file_name, queue):
        super().__init__()
        self._queue = queue
        self._file_name = "created_files/gsr/" + file_name + '.csv'
        backup_file_name = "created_files/gsr/" + file_name + '-backup.csv'
        self._backup_file = open(backup_file_name, 'a')
        self._writer = csv.writer(self._backup_file)
        self._stream_data = []
        self._inintialize_connection()
        self._trigger = []

    def _inintialize_connection(self):
        self._serial = serial.Serial("/dev/rfcomm0", 115200)
        self._serial.flushInput()
        print("port opening, done.")
        # send the set sensors command
        # 4 bytes command:
        #     0x08 is SET_SENSORS_COMMAND
        #     Each bit in the three following bytes are one sensor.
        self._serial.write(struct.pack('BBBB', 0x08 , 0x84, 0x01, 0x00))  #GSR and PPG
        self._wait_for_ack()
        print("sensor setting, done.")

        # Enable the internal expansion board power
        self._serial.write(struct.pack('BB', 0x5E, 0x01))
        self._wait_for_ack()
        print("enable internal expansion board power, done.")

        # send the set sampling rate command

        '''
        sampling_freq = 32768 / clock_wait = X Hz
        2 << 14 = 32768
        '''
        sampling_freq = 128
        clock_wait = math.ceil((2 << 14) / sampling_freq)

        self._serial.write(struct.pack('<BH', 0x05, clock_wait))
        self._wait_for_ack()

        # Inquiry configurations (For finding channels order)
        # Page 16 of This PDF:
        # http://www.shimmersensing.com/images/uploads/docs/LogAndStream_for_Shimmer3_Firmware_User_Manual_rev0.11a.pdf

        self._serial.write(struct.pack('B', 0x01))
        self._wait_for_ack()
        inquiery_response = bytes("", 'utf-8')
        # response_size is 1 packet_type + 2 Sampling rate + 4 Config Bytes +
        # 1 Num Channels + 1 Buffer size
        response_size = 9
        numbytes = 0
        while numbytes < response_size:
            inquiery_response += self._serial.read(response_size)
            numbytes = len(inquiery_response)

        num_channels = inquiery_response[7]
        print("Number of Channels:", num_channels)
        print("Buffer size:", inquiery_response[8])

        # There's one byte for each channel
        # For the meaning of each byte, refer to the above PDF
        channels = bytes("", "utf-8")
        numbytes = 0
        while numbytes < num_channels:
            channels += self._serial.read(num_channels)
            numbytes = len(channels)

        print("Channel 1:", channels[0])
        print("Channel 2:", channels[1])
        print("Channel 3:", channels[2])
        print("Channel 4:", channels[3])
        print("Channel 5:", channels[4])

        # send start streaming command
        self._serial.write(struct.pack('B', 0x07))
        self._wait_for_ack()
        print("start command sending, done.")

    def run(self):
        print("start gsr")
        threading.Thread(target=self._stream_loop).start()
        while(True):
            command = self._queue.get()
            if command is None:
                continue
            elif str(command).isdigit() is True:
                print("Send trigger gsr", command)
                self._trigger = command
            elif command == "terminate":
                self._backup_file.close()
                self._save_to_file()
                break
            else:
                continue

        self._serial.close()

    def _stream_loop(self):
        # read incoming data
       ddata = bytes("", 'utf-8')
       numbytes = 0
       # 1byte packet type + 3byte timestamp + 2 byte X + 2 byte Y +
       # 2 byte Z + 2 byte PPG + 2 byte GSR
       framesize = 14

       try:
          while True:
             while numbytes < framesize:
                ddata += self._serial.read(framesize)
                numbytes = len(ddata)

             data = ddata[0:framesize]
             ddata = ddata[framesize:]
             numbytes = len(ddata)

             # read basic packet information
             (packettype) = struct.unpack('B', data[0:1])
             (timestamp0, timestamp1, timestamp2) = struct.unpack('BBB', data[1:4])

             # read packet payload
             (x, y, z, PPG_raw, GSR_raw) = struct.unpack('HHHHH', data[4:framesize])
             record_time = datetime.datetime.now()

             # get current GSR range resistor value
             Range = ((GSR_raw >> 14) & 0xff)  # upper two bits
             if(Range == 0):
                Rf = 40.2   # kohm
             elif(Range == 1):
                Rf = 287.0  # kohm
             elif(Range == 2):
                Rf = 1000.0 # kohm
             elif(Range == 3):
                Rf = 3300.0 # kohm

             # convert GSR to kohm value
             gsr_to_volts = (GSR_raw & 0x3fff) * (3.0/4095.0)
             GSR_ohm = Rf/( (gsr_to_volts /0.5) - 1.0)

             # convert PPG to milliVolt value
             PPG_mv = PPG_raw * (3000.0/4095.0)

             timestamp = timestamp0 + timestamp1*256 + timestamp2*65536

             #print([packettype[0], timestamp, GSR_ohm, PPG_mv] + self._trigger)

             if self._trigger != []:
                 row = [packettype[0],
                        timestamp,
                        x, y, z,
                        GSR_ohm,
                        PPG_mv,
                        record_time,
                        self._trigger]
                 self._trigger = []
             else:
                 row = [packettype[0],
                        timestamp,
                        x, y, z,
                        GSR_ohm,
                        PPG_mv,
                        record_time]
             self._stream_data.append(row)
             self._writer.writerow(row)

       except KeyboardInterrupt:
          #send stop streaming command
          self._serial.write(struct.pack('B', 0x20))

          print("stop command sent, waiting for ACK_COMMAND")
          self._wait_for_ack()
          print("ACK_COMMAND received.")
          #close serial port
          self._serial.close()
          print("All done")

    def _wait_for_ack(self):
       ddata = ""
       ack = struct.pack('B', 0xff)
       while ddata != ack:
          ddata = self._serial.read(1)

    def _save_to_file(self):
        print(len(self._stream_data))
        with open(self._file_name, 'w') as csv_file:
            writer = csv.writer(csv_file)
            row = ["type", "time stamp", "Acc_x", "Acc_y", "Acc_z",
                   "GSR_ohm",
                   "PPG_mv",
                   "time",
                   "trigger"]
            writer.writerow(row)
            for row in self._stream_data:
                writer.writerow(row)
