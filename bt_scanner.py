from bluepy.btle import Scanner, DefaultDelegate

class ScanDelegate(DefaultDelegate):
    def __init__(self):
        DefaultDelegate.__init__(self)

    def handleDiscovery(self, dev, isNewDev, isNewData):
        if isNewDev:
            #print("Discovered device", dev.addr)
            pass
        elif isNewData:
            #print("Received new data from", dev.addr)
            pass
        return

def bluetooth_scan_to_list(time):
    scanner = Scanner().withDelegate(ScanDelegate())
    devices = scanner.scan(time)

    found=[]
    for dev in devices:
        #print("Device %s (%s), RSSI=%d dB" % (dev.addr, dev.addrType, dev.rssi),dev)
        name=None
        for (adtype, desc, value) in dev.getScanData():
            if desc=='Complete Local Name':
                name=value
        found.append({'addr':dev.addr,
                'rssi':dev.rssi,
                'name':name})
    return found

if __name__=='__main__':
    for dev in bluetooth_scan_to_list(3):
        print(dev)
