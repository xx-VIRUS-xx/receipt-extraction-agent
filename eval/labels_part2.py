from labels_part1 import lab, L
lab("X51005200931.jpg","PERNIAGAAN ZHENG HUI","2018-02-09",436.20,[17.50,114.00,55.00,99.00,86.00,40.00],tax=24.69,subtotal=411.50,incl=False,note="customer GOGIANT ENGINEERING is NOT the vendor; rounding 0.01. NOTE: model output was seen before labelling this one")
for f in ("X51005268275.jpg","X51005301666.jpg"):
    lab(f,"LIGHTROOM GALLERY SDN BHD","2017-11-20",278.80,[90.57,155.66,16.79],tax=15.78,subtotal=263.02,incl=False,note="CREDIT NOTE; the two files are the same image (duplicate)")
lab("X51005268408.jpg","99 SPEED MART S/B","2017-11-20",169.80,[117.90,34.90,4.10,5.89,6.99],tax=9.61,incl=True,alts=[169.78],note="rounding adj .02")
lab("X51005288570.jpg","SECURE PARKING CORPORATION S/B","2018-03-23",1.00,[0.94],tax=0.06,incl=False,note="parking fee 0.94 + GST 0.06")
