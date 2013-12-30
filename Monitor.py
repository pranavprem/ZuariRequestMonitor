import mechanize



#Using Mechanize broser object "br", will be accessing the page through iNotfiy login

br=mechanize.Browser()
br.open('http://119.226.11.102/iNotify')
br.select_form(nr=0) 
#br['txtUserName']= "" Confidential, apparently.
#br['txtPassword']= ""
response=br.submit()
f=open("zuaa.html","w")
for link in br.links():
	if link.text=="Process Execution":
		request = br.click_link(link)
		response = br.follow_link(link)
		br.select_form(nr=0)
		f.write(response.read())

f.close()