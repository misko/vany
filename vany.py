import redis
import threading, queue
import json
import time
import sqlite3
from sqlite3 import Error
from flask import Flask,render_template,request,session
import os
import datetime
import math
import random
from flask_socketio import SocketIO
from bt_scanner import bluetooth_scan_to_list

VERSION=1
pubsub_channel='vany'

app = Flask(__name__)
app.secret_key = 'my super secret key'.encode('utf8')

socketio = SocketIO(app)

db_folder="./db"
if not os.path.exists(db_folder):
  os.makedirs(db_folder)

r = redis.Redis(host='localhost', port=6379, db=0)

def get_hash():
  return random.getrandbits(128)

def timestamp():
  return datetime.datetime.now()

class Message:
  def __init__(self,version,_from,_to,_type,data,sid=-1,_hash=None):
    self.version=version
    self._from=_from
    self._to=_to
    self._type=_type
    self.data=data
    if _hash==None:
      _hash=get_hash()
    self._hash=_hash
    self.sid=sid

  def to_dict(self):
    header,data=self.to_tuple()
    return {'header':header,'data':data}

  def to_tuple(self):
    return ({'from':self._from,
          'to':self._to,
          'type':self._type,
          'hash':self._hash,
          'version':self.version,
          'sid':self.sid},self.data)
   
  def encode(self):
    return json.dumps(self.to_tuple())
    
  def decode(s):
    header,data=json.loads(s)
    return Message(version=header['version'],
        _from=header['from'],
        _to=header['to'],
        _type=header['type'],
        _hash=header['hash'],
        sid=header['sid'],
        data=data)

  def make_response(self,data=None):
      return Message(self.version,self._from,self._to,self._type+"_response",data=data,sid=self.sid,_hash=self._hash)

  def __repr__(self):
      return "V:%s, F:%s, T:%s, TY:%s SID:%s , %s" % (self.version,self._from,self._to,self._type,self.sid,self._hash)

class Node:
  def __init__(self,name,wait=0.001):
    self.name = name
    self.r = redis.Redis(host='localhost', port=6379, db=0)
    self.p = self.r.pubsub()
    self.wait=wait
    self.run_thread=threading.Thread(target=self.run)
    self.running=True
    self.should_run=True
    self.run_thread.start()
    self.message_handlers = {'hello':self.hello_handler}

  def hello_handler(self,m):
    response=m.make_response(data={})
    self.r.publish(pubsub_channel, response.encode()) 

  def run(self):
    self.running=False

class BluetoothScanner(Node):
  def __init__(self,name,simulate=False):
    super().__init__(name)
    self.simulate=simulate
    self.message_handlers['btscan']=self.scan_handler

  def scan_handler(self,m):
    discovered_devices=bluetooth_scan_to_list(m.data['time'])
    response=m.make_response(data={'discovered_devices':discovered_devices})
    print("SCAN RESPONSE",response,response.data)
    self.r.publish(pubsub_channel, response.encode()) 

  def run(self):
    print("RUNNING",self.name)
    #make sure the db is setup
    self.sqlite = sqlite3.connect(db_folder+"/"+self.name+".db")
    self.sqlite.cursor().execute("""CREATE TABLE IF NOT EXISTS devices (
                                    ts timestamp NOT NULL,
                                    addr str NOT NULL,
                                    name str 
                                );""")
    self.sqlite.commit()
    
    #join the pubsub to respond to events
    self.p.subscribe(pubsub_channel)

    last_simulate=None
    while self.should_run:
      m=self.p.get_message()
      while m:
        if m['type']=='message':
          m=Message.decode(m['data'])
          if m._type in self.message_handlers:
            self.message_handlers[m._type](m)
        m=self.p.get_message()
      #lets check for a new voltage
      time.sleep(self.wait)
    print("Bluetooth scanner exiting")


class TeslaBattery(Node):
  def __init__(self,name,simulate=False):
    super().__init__(name)
    self.simulate=simulate
    #id integer PRIMARY KEY,

  def add_voltage(self,voltage):
    sqlite_insert_with_param = """INSERT INTO 'voltage'
  		  ('ts', 'volts') 
  		  VALUES (?, ?);"""
    data_tuple = (timestamp(), voltage)
    self.sqlite.cursor().execute(sqlite_insert_with_param, data_tuple) 
    self.sqlite.commit()

  def add_amps(self,amps):
    sqlite_insert_with_param = """INSERT INTO 'amps'
  		  ('ts', 'amps') 
  		  VALUES (?, ?);"""
    data_tuple = (timestamp(), amps)
    self.sqlite.cursor().execute(sqlite_insert_with_param, data_tuple) 
    self.sqlite.commit()

  def run(self):
    print("RUNNING",self.name)
    #make sure the db is setup
    self.sqlite = sqlite3.connect(db_folder+"/"+self.name+".db")
    self.sqlite.cursor().execute("""CREATE TABLE IF NOT EXISTS voltage (
                                    ts timestamp NOT NULL,
                                    volts real NOT NULL 
                                );""")
    self.sqlite.cursor().execute("""CREATE TABLE IF NOT EXISTS amps (
                                    ts timestamp NOT NULL,
                                    amps real NOT NULL 
                                );""")
    self.sqlite.commit()
    
    #join the pubsub to respond to events
    self.p.subscribe(pubsub_channel)

    last_simulate=None
    while self.should_run:
      m=self.p.get_message()
      while m:
        if m['type']=='message':
          m=Message.decode(m['data'])
          if m._type in self.message_handlers:
            self.message_handlers[m._type](m)
        m=self.p.get_message()
      #lets check for a new voltage
      if self.simulate and last_simulate==None or (timestamp().timestamp()-last_simulate)>3:
        v=math.sin(datetime.datetime.now().timestamp()/10)+10
        self.add_voltage(v)
        self.add_amps(v/2)
        last_simulate=timestamp().timestamp()
      time.sleep(self.wait)
    print("Tesla battery exiting")

##FLASK 

  
class WebServer(Node):
  def __init__(self,name):
    super().__init__(name)
    self.requests_q = queue.Queue()
    self.responses_q = queue.Queue()

  def send_request(self,request):
    self.requests_q.put(request)

  def run(self):
    self.p.subscribe(pubsub_channel)
    while self.should_run:
      #check the pubsub
      m=self.p.get_message()
      while m:
        if m['type']=='message':
          m=Message.decode(m['data'])
          print(m)
          if m._type in self.message_handlers:
            self.message_handlers[m._type](m)
          #pass on hello response to all clients
          if m._type=='hello_response':
            socketio.emit('hello_response',m.to_tuple())
          if m._type=='btscan_response':
            print("ROOM",m.sid)
            socketio.emit('btscan_response',m.to_dict(),room=m.sid)
        m=self.p.get_message()
      #check for requests from web
      while not self.requests_q.empty(): 
        client_request=self.requests_q.get(block=False)     
        if client_request['to']=='hello':
          vany_request=Message(version=VERSION,_from=self.name,_to="All",_type="hello",data={})
          self.r.publish(pubsub_channel, vany_request.encode()) 
        elif client_request['to']=='BluetoothScanner':
          vany_request=Message(version=VERSION,_from=self.name,_to="All",_type="btscan",data=client_request['data'],sid=client_request['sid'])
          self.r.publish(pubsub_channel, vany_request.encode()) 
      time.sleep(self.wait)
    print("Web server exiting") 
    self.running=False


#@app.before_request
#def do_something_whenever_a_request_comes_in():
#    if 'sid' not in session:
#        session['sid']=get_hash()

@app.route('/dash')
def dash():
	return render_template('example.html')
@app.route('/')
def hello_world():
    ws.send_request({'type':'hello'})
    return 'Hello, World!'

@app.route('/btscan')
def http_btscan():
    ws.send_request({'type':'btscan','sid':sid,'data':{'time':3}})
    return 'Hello, World btscan!'

@app.route('/blocking_voltage')
def blocking_voltage():
    m=Message(version=VERSION,_from="WebClient",_to="WebServer",_type="Query",data={'TeslaBattery',})

@socketio.on('message')
def handle_message(data):
    print('received message: ' + data)


@socketio.on('join')
def on_join(data):
    print("SOMEONE JOINED")

@socketio.on('json')
def handle_json(json):
    print('received json: ' + str(json))

@socketio.on('my event')
def handle_my_custom_event(json):
    print('custom event received json: ' + str(json))

@socketio.on('vany_request')
def handle_query(json):
    print("GOT SOCKET REQUEST")
    json['sid']=request.sid
    ws.send_request(json)
    #socketio.emit('query_response', {'voltage':4.12})

if __name__=='__main__':
  print("Welcome to vany!")
  #try to make some nodes
  ws=WebServer(name="FlaskWebserver")
  ts=TeslaBattery(name="TeslaBattery",simulate=True)
  bt=BluetoothScanner(name="BluetoothScanner")
  #test message encode and decocde
  m=Message(version=VERSION,_from="Battery",_to="All",_type="Voltage",data=3.0)
  m_str=m.encode()
  m=Message.decode(m_str)
  socketio.run(app,host = '0.0.0.0')
  #app.run()

