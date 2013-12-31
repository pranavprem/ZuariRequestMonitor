import mechanize
flag=0

import smtplib
server = smtplib.SMTP('smtp.gmail.com:587')
server.ehlo()
server.starttls()
server.login("username@gmail.com", "")#add username and password

while True:

	#Using Mechanize broser object "br", will be accessing the page through iNotfiy login

	br=mechanize.Browser()
	br.open('http://119.226.11.102/iNotify')
	br.select_form(nr=0) 
	br['txtUserName']= ""
	br['txtPassword']= ""
	response=br.submit()
	f=open("zuaa.html","w")
	for link in br.links():
		if link.text=="Process Execution":
			request = br.click_link(link)
			response = br.follow_link(link)
			br.select_form(nr=0)
			f.write(response.read())

	f.close()


	#the page now stored in 'zuaa.html', will be analysed


	br.close()
	f=open("zuaa.html","r")
	a=f.read()
	f.close()
	import re
	b="".join(re.findall('>[0-9]+<',a))
	print b
	c=b[1:-1]
	c=c.split('<>')
	seq=["Fertilizer SO and delivery creation", "Agri SO and delivery creation", "Fertilizer SO delivery confirmation", "Agri SO delivery confirmation", "Fertilizer STA Creation", "Agri STA Creation", "Fertilizer STA Delivery", "Agri STA delivery", "Fertilizer STA goods receipt","Agri STA goods receipt","FertilizerDSP creation","agri production consumption","Fertilizer DSP delivery","agri raw material grr","Fertilizer DSP goods receipt"]
	f.close()

	#c now contains all the values of pending on the web page. Taken smartly using real expressions. Thank you, Python.
	usestr="nil"
	normalagain=0
	for i in range(0,len(c)):
		c[i]=int(c[i])
		if c[i]>50 and flag==0:
			flag=1
			usestr=""+seq[i]+"  "+str(c[i])

	if usestr=="nil":
		for i in range(0,len(c)):
			if c[i]>50:
				break

	if i==(len(c)-1) and flag==1:
			flag=0
			normalagain=1
		

	#Data is hence mined. That leaves final bit. Sending the ALERT email.
	if flag==1 and usestr!="nil":
		msg = "\r\n".join(["From:", "To: ", "Subject: ", "", +usestr])
		server.sendmail("From", "To", msg)
		
	
	if normalagain==1:
		msg = "\r\n".join(["From: ", "To: ", "Subject:", "", "Values are normal again \n\n"])
		server.sendmail("From", "To", msg)
		
	print flag, normalagain