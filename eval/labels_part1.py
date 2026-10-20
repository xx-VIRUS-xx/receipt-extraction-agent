# Hand labels, read directly from the images. items = line-item amounts (line totals).
# total_alts = other defensible readings of "total" (e.g. pre-rounding amount).
L = {}
def lab(f, vendor, date, total, items, tax=None, subtotal=None, incl=None, alts=(), vendor_alts=(), note="", legible=True, currency="MYR", items_uncertain=False, date_alts=()):
    L[f] = dict(vendor=vendor, vendor_alts=list(vendor_alts), date=date, total=total, total_alts=list(alts), items=items,
                tax=tax, subtotal=subtotal, prices_include_tax=incl, currency=currency, legible=legible, note=note, items_uncertain=items_uncertain, date_alts=list(date_alts))

lab("X00016469671.jpg","OJC MARKETING SDN BHD","2019-01-02",170.00,[170.00],note="no currency symbol printed; MYR from Johor address")
lab("X51005230605.jpg","PETRON BKT LANJAN SB","2018-02-01",4.90,[3.00,1.90],tax=0.28,incl=True)
lab("X51005442388.jpg","CONTENTO","2018-03-20",21.60,[11.80,4.90,1.00,3.90],note="handwritten annotations on receipt")
lab("X51005230616.jpg","GERBANG ALAF RESTAURANTS SDN BHD","2018-01-18",38.90,[25.40,12.50,1.00],tax=2.20,incl=True,vendor_alts=["MCDONALD'S"])
lab("X51005230621.jpg","SIN LIANHAP SDN BHD","2018-02-05",7.30,[2.00,5.30],tax=0.41,subtotal=7.30,incl=True)
lab("X51005230648.jpg","CROSS CHANNEL NETWORK SDN. BHD.","2018-01-29",6.35,[6.36],tax=0.36,incl=True,alts=[6.36],note="rounding -0.01; payable 6.35")
lab("X51005230657.jpg","CROSS CHANNEL NETWORK SDN. BHD.","2017-12-31",7.95,[1.80,1.91,3.71,0.53],tax=0.45,incl=True)
lab("X51005230659.jpg","SWC ENTERPRISE SDN BHD","2018-01-08",8.00,[8.00],tax=0.45,incl=True)
