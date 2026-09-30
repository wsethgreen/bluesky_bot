NHL & NCAAH API
The Highlightly NHL & NCAAH API provides real time hockey data including live scores, video highlights, match statistics, player profiles and stats, lineups, standings, predictions, head to head data and live game events.

Coverage spans the NHL regular season, playoffs and the Stanley Cup Finals, plus 10+ NCAA Division I hockey conferences including Hockey East, the Big Ten, the NCHC and the ECAC.

Base URL
nhl.highlightly.net
Endpoints
18
Format
JSON only
Version
3.1.4
Also available with an All Sports API subscription at sports.highlightly.net/nhl. See base URLs.

Where to start
Read Authentication first, since every request needs an API key. Pagination and limits covers paging, rate limits and what your plan might restrict.

If you want	Start at
Goals, saves and game recaps	Highlights
Schedules, live scores and game detail	Matches
Conference tables	Standings
Player scoring and penalty records	Player statistics
NHL and college in one API
league separates the two on teams and matches. Highlights and odds use leagueName, and standings uses leagueType. Filter on it when your product covers only one, since the same endpoints serve both.

Standings lists eight groupings alongside the Eastern and Western Conferences.

Base URLs
NHL and college hockey data is available two ways. An NHL & NCAAH API subscription covers this API alone. An All Sports API subscription covers it and all other Highlightly sports under one key. Either one can be bought directly from Highlightly or through RapidAPI, which gives four base URLs.

Subscription	Platform	Base URL
NHL & NCAAH API	Highlightly	https://nhl.highlightly.net
NHL & NCAAH API	RapidAPI	https://nhl-ncaah-api.p.rapidapi.com
All Sports API	Highlightly	https://sports.highlightly.net/nhl
All Sports API	RapidAPI	https://sport-highlights-api.p.rapidapi.com/nhl
Every endpoint in this reference is available on all four. On Highlightly, one API key works for every product, and the data returned depends on your subscription to that product. On RapidAPI, each product is separate and needs its own API key.

To call an endpoint, append its path to your base URL. /matches becomes https://nhl.highlightly.net/matches with an NHL & NCAAH API subscription and https://sports.highlightly.net/nhl/matches with an All Sports API subscription. Parameters, responses and refresh intervals are the same on both, and every endpoint page shows both routes.

Your plan affects the data retrieved. On the Basic and Free plans highlights might have certain restrictions, and odds and geo restrictions are not available, with either subscription. See Pagination and limits.

What changes with an All Sports API subscription
The headers have the same names on every base URL. x-rapidapi-key is sent with every request, and x-rapidapi-host is sent only when calling through RapidAPI. What changes is the path and the host header, and on RapidAPI the key.

NHL & NCAAH API	All Sports API
Path	/matches	/nhl/matches
x-rapidapi-key on Highlightly	Your Highlightly API key	Your Highlightly API key
x-rapidapi-key on RapidAPI	Your NHL & NCAAH API key	Your All Sports API key
x-rapidapi-host	nhl-ncaah-api.p.rapidapi.com	sport-highlights-api.p.rapidapi.com
On Highlightly, the same API key works on nhl.highlightly.net and sports.highlightly.net, and the data returned depends on your subscription to each product. On RapidAPI, the NHL & NCAAH API and the All Sports API are separate products, so each needs its own API key. One All Sports API quota covers every sport you call. See All Sports authentication.

Highlightly or RapidAPI
Both platforms give you analytics, billing settings and key management. RapidAPI does not offer custom plans or long term plan discounts.

Accounts are not synced across platforms

An account created on Highlightly is separate from an account created on RapidAPI.

Create an account at Highlightly or through RapidAPI. Plans and limits are listed on the NHL API page and the All Sports API page.

OpenAPI specification
Import the OpenAPI 3.0 document into Postman or Insomnia, or generate a client from it.

Download docs.json
Support
For feature requests, questions, private plans or business enquiries, contact support@highlightly.net. You can also start a discussion or send a private message through RapidAPI.