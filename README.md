

this used for participating in myorder hackerthon competition

you should learn how to add bot and key in Slack API
and
get service-account.json from Google Cloud Console
and
get Google AI API key from this https://aistudio.google.com/

then run this main.py on ngrok or host whatever

give {url}/slack/events in event subscribe in setting of Slack API 

Workflow:

Slack --> Service --> LLM(Gemini) --> Service --> GoogleSheet --> Reply to Slack
