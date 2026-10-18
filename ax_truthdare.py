"""Truth or Dare for Rumi: /td lobby, /truth, /dare.
Register in main.py:  import ax_truthdare ; ax_truthdare.register(application)
"""
import html
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

TRUTHS = [
    "What is the weirdest thing you do when you are completely alone?",
    "If someone went through your saved Instagram posts right now, what would they find?",
    "What is the most awkward text you've ever sent to your mom by mistake?",
    "What is the most petty thing you've done after an argument?",
    "What is the worst gift you've ever given someone?",
    "Have you ever blamed a mistake you made on someone else?",
    "What is the weirdest rumor you've ever heard about yourself?",
    "If you had to delete all but three apps on your phone, which ones stay?",
    "What is the most embarrassing song on your playlist?",
    "What is the most awkward public interaction you've had this week?",
    "What is a lie you told that you still haven't confessed to?",
    "Who is the last person you muted or restricted on social media?",
    "What is the weirdest habit you have before going to sleep?",
    "Have you ever taken credit for someone else's work?",
    "What is the most you've ever spent on an in-game purchase?",
    "What is the most embarrassing nickname you've ever been given?",
    "What is the worst piece of advice you've ever given someone?",
    "What is the most childish argument you've had recently?",
    "Have you ever photoshopped your own pictures before posting them?",
    "What is the most awkward thing you've said during a moment of silence?",
    "Have you ever forgotten someone's name while talking to them?",
    "What is the weirdest thing you've ever eaten out of politeness?",
    "What is the most ridiculous fear you had as a child?",
    "What is the most awkward autocorrect fail you've ever experienced?",
    "Have you ever pretended to know a celebrity or band to sound cool?",
    "Have you ever secretly hated a gift but acted thrilled?",
    "What is the weirdest thing you've ever bought online late at night?",
    "Have you ever been caught lying in this exact Telegram group?",
    "Who in this chat do you think has the best fashion sense?",
    "What is the most embarrassing thing you've searched on YouTube?",
    "Have you ever pretended to be asleep when someone walked in?",
    "What is the worst meal you've ever cooked?",
    "What is the worst habit you are currently trying to break?",
    "Have you ever pretended to not see someone you knew in public?",
    "What is the most awkward compliment you've ever received?",
    "Have you ever accidentally insulted someone to their face?",
    "What is the most ridiculous thing you've believed for way too long?",
    "What is the worst text you could possibly receive right now?",
    "Have you ever secretly thrown away food someone cooked for you?",
    "Have you ever pretended to lose your voice to avoid speaking?",
    "What is the worst excuse you've used for being late?",
    "What is the weirdest place you've ever fallen asleep?",
    "Have you ever accidentally liked your own post?",
    "Have you ever faked a laugh so hard you coughed?",
    "What is the most embarrassing thing you've searched for on Google this week?",
    "Have you ever accidentally sent a risky or embarrassing message to the wrong chat? What happened?",
    "If we looked at your YouTube watch history right now, what is the weirdest video we would find?",
    "Who in this group chat do you think has the worst sleep schedule?",
    "What is the most embarrassing username you have ever used?",
    "If you had to delete one app from your phone forever, what would it be?",
    "What is the most expensive thing you have bought but never used?",
    "What is a lie you told that escalated way too far?",
    "Have you ever cried over a fictional character? Who was it?",
    "What is a weird food combination that you genuinely enjoy?",
    "If you could read the mind of one person in this group chat, who would it be?",
    "What is the most petty reason you have ever blocked someone?",
    "What is a movie or show you secretly love but pretend to hate?",
    "Have you ever laughed at a joke you completely didn't understand just to fit in?",
    "What is the worst excuse you have ever used to cancel plans?",
    "If you had to trade lives with someone in this group for a day, who would it be?",
    "Have you ever dropped food on the floor, picked it up, and eaten it?",
    "What is the most childish thing you still do to this day?",
    "If you had to survive a zombie apocalypse with 3 people from this group, who are you picking?",
    "What is the most irrational fear you have?",
    "What is the worst haircut you have ever had?",
    "What is a controversial opinion you hold that you rarely share?",
    "If you were given $10,000 but could only spend it in 1 hour, what would you buy?",
    "What is the weirdest dream you have ever had about someone you know?",
    "What is a talent you have that is completely useless?",
    "If you could erase one memory from your brain, what would it be?",
    "What is one thing you pretend not to care about? Be completely honest.",
    "Who was the last person you checked on Telegram? Why did you check them?",
    "What is your most useless talent? Show it if someone asks.",
    "Have you ever ignored someone on purpose? What was the reason?",
    "What is your most embarrassing nickname? Who gave it to you?",
    "What is one habit you wish you could delete? Be honest about it.",
    "What is your biggest pet peeve? What instantly annoys you?",
    "Who knows you better than most people? Why do you think they do?",
    "What is your favorite compliment? Who gave you the best one?",
    "Have you ever pretended to understand something? What was it?",
    "What is the funniest lie you have told? Did anyone believe you?",
    "What is your comfort food? How often do you eat it?",
    "What is the first thing you notice about someone? Be honest.",
    "What is your weirdest fear? Does it still scare you?",
    "What is one rule you often break? Why do you break it?",
    "Have you ever laughed at the wrong moment? What happened?",
    "What is your most-used emoji? Why that one?",
    "What is something you wish people understood about you? Say it honestly.",
    "Have you ever blamed someone for something you did? What happened afterward?",
    "What is one thing you secretly want to learn? Why haven't you learned it yet?",
    "Who makes you laugh the most? What do they usually do?",
    "What is one food you absolutely cannot stand? What makes it so bad?",
    "What is your biggest weakness in games? Be brutally honest.",
    "What is something you regret saying? Would you take it back?",
    "What is your favorite thing about your personality? Don't be shy.",
    "What is something you wish you were better at? What stops you?",
    "What is your favorite type of humor? What always gets you laughing?",
    "What is the strangest dream you remember? What happened in it?",
    "What is one thing you dislike about group chats? Be specific.",
    "Have you ever accidentally reacted to an old message? What did you do afterward?",
    "What is one thing you always overthink? How often does it happen?",
    "What is your most random obsession? How long has it lasted?",
    "What is the funniest thing a friend has done? Tell the story briefly.",
    "Who would you trust with your biggest secret? What makes them trustworthy?",
    "What is your worst habit while texting? Do people complain about it?",
    "What is one thing you are surprisingly good at? When did you discover it?",
    "What is the strangest food combination you enjoy? Would you recommend it?",
    "What is one thing you find overrated? Why?",
    "What is your favorite meme format? Which one always makes you laugh?",
    "What is one thing you cannot leave home without? Why is it important?",
    "What is your most memorable school moment? What happened?",
    "What is one thing that makes you instantly trust someone? What's your biggest green flag?",
    "What is one thing that makes you instantly distrust someone? What's your biggest red flag?",
    "What is your favorite fictional character? What do you like about them?",
    "What is your most embarrassing fashion choice? Would you wear it again?",
    "What is one thing you wish you had more time for? What would you do with it?",
    "Have you ever laughed so hard you couldn't speak? What caused it?",
    "What is your most random skill? How did you learn it?",
    "What is something you used to hate but now like? What changed?",
    "What is one thing you would buy immediately if money didn't matter? What makes it worth having?",
    "Have you ever made a completely unnecessary purchase? What did you buy?",
    "What is your favorite game? What makes it addictive?",
    "What is your most embarrassing typo? Did anyone screenshot it?",
    "What is one thing you would never eat? Even if someone offered you money?",
    "Who is the funniest person you know? What makes them funny?",
    "What is one thing you have changed your mind about recently? Why?",
    "What is something you secretly find satisfying? It can be completely random.",
    "What is your favorite app? How much do you use it?",
    "What is something you wish you could do instantly? Why that skill?",
    "What is one thing you are currently curious about? What made you curious?",
    "What is the longest you've stayed awake? What kept you awake?",
    "What is something you are secretly competitive about? Who usually wins?",
    "Have you ever pretended not to see a message? Why?",
    "What is your favorite random fact? Where did you learn it?",
    "What is the funniest misunderstanding you've had? How was it resolved?",
    "What is something you used to believe as a kid? When did you discover the truth?",
    "What is one thing that instantly makes you nostalgic? What memory does it bring back?",
    "What is your favorite type of notification? Which one makes you instantly check your phone?",
    "What is something you have recently learned? Was it useful?",
    "What is one thing you are better at than your friends? Don't be modest.",
    "What is your favorite thing to do when bored? What do you usually end up doing?",
    "What is one thing you want to accomplish this year? Why does it matter?",
    "What is your favorite kind of joke? Give an example without targeting anyone.",
    "What is something you would like to collect? Why that particular thing?",
    "Have you ever changed your username randomly? What inspired it?",
    "What is one thing you wish people knew about you? Say something genuine.",
    "What is the funniest thing you've witnessed recently? What happened?",
    "What is your biggest harmless guilty pleasure? How often do you enjoy it?",
    "Have you ever pretended to be asleep? What were you avoiding?",
    "What is your favorite emoji combination? When do you use it?",
    "What is one thing you always check twice? Why?",
    "What is your most random fear? When did you first notice it?",
    "What is something you could teach someone in five minutes? What would you teach?",
    "What is one thing that always makes you smile? Why?",
    "What is something you wish you had started earlier? What would you do differently?",
    "What is your funniest childhood mistake? What happened?",
    "What is one thing you are currently obsessed with? How did it start?",
    "What is one thing you would change about your daily routine? What would improve?",
    "What is the funniest excuse you've ever used? Did anyone believe it?",
    "What is one thing that makes you instantly comfortable? Why?",
    "Have you ever rehearsed what to say before texting? What kind of message?",
    "What is one thing you are surprisingly patient with? What makes it easy?",
    "What is one thing you have absolutely no patience for? Why?",
    "Have you ever laughed during a serious moment? What caused it?",
    "What is your favorite fictional villain? What makes them interesting?",
    "What is one thing you would change about your room? What would you add?",
    "What is something you wish existed? What would it do?",
    "What is one thing you always overprepare for? Why?",
    "What is your favorite kind of compliment to give? Who deserves one?",
    "What is one thing you wish you could master? How would you use it?",
    "Have you ever started something and never finished it? What was it?",
    "What is your favorite type of personality? What draws you to it?",
    "What is one thing you would never delete from your phone? Why keep it?",
    "What is one thing that makes you feel accomplished? What gives you that feeling?",
    "What is one thing you would do if you had an entire day free? No responsibilities allowed.",
    "What is one thing you wish you had more confidence doing? Why?",
    "What is your favorite memory from this year? Why?",
    "Have you ever made someone laugh unintentionally? What did you say?",
    "What is one thing you never get tired of hearing? Why?",
    "What is something you wish you could explain perfectly? Why?",
    "What is your funniest accidental screenshot? What was on the screen?",
    "What is one thing you always notice first in a new place? Why do you notice it?",
    "What is your favorite kind of compliment about your personality? Which one feels best?",
    "What is one thing you are currently looking forward to? Why?",
    "Have you ever made a decision purely because of a coin toss? What happened?",
    "What is one thing you would change about your online profile? What would you change?",
    "Have you ever completely misunderstood someone's joke? What did you think they meant?",
    "What is one thing you would happily learn from a friend? Who would teach you?",
    "What is your favorite harmless conspiracy theory? Do you actually believe it?",
    "What is one thing you would put in your dream house? Why that item?",
    "Have you ever had a completely unexpected friendship? How did it begin?",
    "What is one thing you would never get bored of discussing? Why?",
    "What is your favorite thing about your personality? Give one example.",
    "Have you ever unexpectedly impressed yourself? What did you do?",
    "What is one thing you think you understand better than most people? Explain briefly.",
    "What is something you are secretly proud of? Even if it seems small.",
    "What is something you are terrible at? Would you ever admit it publicly?",
    "What is your biggest procrastination habit? How often do you do it?",
    "What is your strangest daily habit? How did it start?",
    "Have you ever stayed awake way too late for something silly? What were you doing?",
    "What is one thing you have never tried? Would you try it someday?",
    "Have you ever pretended to be busy? What were you actually doing?",
    "What is one thing that instantly improves your mood? Why does it work?",
    "Have you ever forgotten someone's birthday? How did you handle it?",
    "Have you ever practiced a conversation beforehand? What was the situation?",
    "What is something you always say? Do your friends notice it?",
    "Have you ever forgotten why you entered a room? How often does that happen?",
    "What is your biggest distraction? How easily does it distract you?",
    "What is one thing you always postpone? Why?",
    "Have you ever forgotten someone's name immediately? How did you hide it?",
    "Have you ever laughed at your own joke? Was it actually funny?",
    "What is something you do when nobody is watching? Be honest.",
    "What is your most repeated thought? Why does it keep coming back?",
    "Have you ever accidentally spoiled something for someone? How did you react?",
    "What is your biggest \"I should have known better\" moment? What did you learn?",
    "Have you ever forgotten an important task? What happened?",
    "Have you ever joined a conversation halfway through? Did you understand anything?",
    "What is your funniest group-chat memory? What made everyone laugh?",
    "What is one thing you always carry? Why?",
    "What is your favorite nickname? Who gave it to you?",
    "What is one thing that can ruin your mood instantly? Why?",
    "Have you ever accidentally liked something while scrolling? How did you react?",
    "What is your most chaotic habit? Does anyone know?",
    "Have you ever made a plan and abandoned it immediately? What happened?",
    "Have you ever accidentally said something out loud? What happened?",
    "Have you ever gotten lost somewhere familiar? How did that happen?",
    "Have you ever accidentally ignored someone you knew? Did they notice?",
    "Have you ever accidentally sent a message too early? What happened?",
    "Have you ever accidentally revealed a surprise? What happened?",
    "Have you ever made a joke that nobody understood? What happened?",
    "Have you ever had a plan go hilariously wrong? What happened?",
    "Have you ever surprised yourself with your own reaction? What happened?",
    "If your current life had a title, what would it be? Make the title creative.",
    "What is something you would try if embarrassment didn't exist? Keep it harmless.",
    "What is the most random thing that makes you happy? Don't overthink it.",
    "What is something you find strangely satisfying? Explain your choice.",
    "What is one thing you would improve about your phone? What feature would you add?",
    "What is one thing you wish you were fearless about? What would you try?",
    "What is your favorite kind of challenge? What makes it fun?",
    "What is your funniest reaction to a surprise? What happened?",
    "What is your favorite childhood game? Would you play it now?",
    "What is your favorite childhood cartoon? Would you still watch it?",
    "What is your favorite fictional world? Would you live there?",
    "What is one thing you wish you could change about your routine? What would you replace it with?",
    "What is one thing you would change about yesterday? Keep it realistic.",
    "What is your favorite late-night activity? What makes it better at night?",
    "What is one thing you dislike about online conversations? Why?",
    "What is your favorite random tradition? Where did it come from?",
    "What is your favorite type of person to talk to? Why?",
    "What is one skill you admire in other people? Why?",
]

DARES = [
    "Send the group your most-used emoji. No explanation allowed.",
    "Send a message using only emojis. Let everyone guess what it means.",
    "Type your next message with your eyes closed. Don't correct the mistakes.",
    "Send \"I have an important announcement.\" to the group. Then reveal something completely harmless.",
    "Rename yourself for 10 minutes. Pick the funniest name you can think of.",
    "Send a random sticker. It must match absolutely nothing.",
    "Describe yourself using exactly three words. Make at least one word unexpected.",
    "Send a voice message saying \"I have no idea what I'm doing.\" Say it dramatically.",
    "Use only lowercase letters for your next five messages. Don't break the rule.",
    "Invent a ridiculous superhero name. Give them one silly power.",
    "Send a message containing exactly seven words. Make it funny.",
    "Type a sentence backwards. Let someone decode it.",
    "Send a random harmless compliment to someone. Don't explain why.",
    "Create a fake movie title about your day. Make it dramatic.",
    "Send \"breaking news\" to the group. The news must be something completely harmless.",
    "Send your best \"NPC dialogue.\" Stay in character for one message.",
    "Make a three-word slogan for yourself. It must sound like an advertisement.",
    "Send a message using only questions. Ask at least three.",
    "Invent a new emoji. Describe what it looks like.",
    "Send the funniest safe GIF you can find. No context.",
    "Write your name using emojis. Let others guess it.",
    "Talk like a robot for your next three messages. Be unnecessarily formal.",
    "Create a fake advertisement for your phone. Make it hilariously exaggerated.",
    "Send a weather report for your room. Include temperature, mood, and forecast.",
    "Give yourself a ridiculous royal title. Use it once in the chat.",
    "Send a sentence where every word starts with the same letter. Make it understandable.",
    "Describe your day like a sports commentator. Make it dramatic.",
    "Send the group a completely random fact. It must be something you actually know.",
    "Write a two-line poem about snacks. Make it unnecessarily emotional.",
    "Use three random emojis to describe your mood. Don't explain them.",
    "Create a fake news headline about yourself. Keep it harmless.",
    "Write your next message as if you're a medieval king. Keep it short.",
    "Send a sentence containing three animal sounds. Make it make sense somehow.",
    "Send a voice message saying the alphabet dramatically. Pretend you're announcing a championship.",
    "Make up a new holiday. Give it a name and one tradition.",
    "Describe your phone as if it were a person. Give it a personality.",
    "Send one harmless unpopular opinion. Be prepared to defend it.",
    "Write your next message like a detective. Make it mysterious.",
    "Make a five-word story. Every word must matter.",
    "Send an emoji that represents your personality. Let the group judge your choice.",
    "Invent a ridiculous law for this group. Explain the punishment using only emojis.",
    "Send \"I have been summoned.\" in the group. Refuse to explain for one minute.",
    "Describe your favorite food like a luxury product. Make it sound expensive.",
    "Write a dramatic farewell message. Then immediately say \"I'm back.\"",
    "Send your next three messages as movie quotes you invent. Make them sound cinematic.",
    "Create a fake achievement for yourself. Give it a ridiculous title.",
    "Send a voice note saying one sentence in a cartoon voice. Keep it short and funny.",
    "Make a new group slogan. Everyone gets to rate it.",
    "Describe the person above you using three positive words. No roasting allowed.",
    "Send a message using exactly ten emojis. Make the emojis tell a story.",
    "Pretend you're a game NPC. Give the next person a harmless quest.",
    "Invent a new game. Explain its rules in two sentences.",
    "Write a sentence with every word starting with \"S.\" Keep it understandable.",
    "Send a random three-word prophecy. Don't explain it.",
    "Describe your room like a travel destination. Include one attraction.",
    "Write a tiny horror story. Keep it spooky, not graphic.",
    "Make a fake weather forecast for tomorrow. Include one ridiculous prediction.",
    "Send a voice note saying your name like a sports announcer. Make it unnecessarily epic.",
    "Write your next message as a pirate. Arrr, obviously.",
    "Send the group a harmless challenge. Make it something everyone can do.",
    "Describe yourself as a video-game character. Include one special ability.",
    "Write a motivational speech about drinking water. Make it sound like a war speech.",
    "Make a two-line rap about your favorite snack. Keep it clean.",
    "Invent a new sport. Give it one ridiculous rule.",
    "Give yourself a superhero origin story. Keep it to three sentences.",
    "Send \"This changes everything.\" to the group. Then reveal something silly.",
    "Describe your morning like an action movie. Make ordinary events sound epic.",
    "Create a fictional app. Give it a name and purpose.",
    "Write a sentence without using the letter \"a.\" Don't edit it afterward.",
    "Pretend you're a famous chef. Describe your imaginary signature dish.",
    "Make a new emoji combination. Give it a meaning.",
    "Give the group a harmless mission. Everyone must know exactly what to do.",
    "Send a voice note using your most dramatic voice. Say something completely normal.",
    "Make up a conspiracy about why socks disappear. Make it surprisingly convincing.",
    "Send a random sticker and call it your new mascot. Give the mascot a name.",
    "Make a fake horoscope for yourself. Include one ridiculous prediction.",
    "Write a one-line poem about your phone. Make it unnecessarily emotional.",
    "Send your mood as a three-emoji equation. Example: 😴 + ☕ = 😎.",
    "Make a fake trailer for your life. Write three dramatic lines.",
    "Write your next message as a detective's report. Investigate something harmless nearby.",
    "Send five emojis that tell a complete story. No words.",
    "Make a fake advertisement for your favorite snack. Add a ridiculous slogan.",
    "Send your next message as if you're royalty. Nobody is allowed to question your authority.",
    "Give yourself a gamer-style achievement. Include the achievement title and description.",
    "Make a fake documentary narration. Narrate what someone in the group is doing.",
    "Write a fake newspaper headline about the group. Keep it harmless.",
    "Send a compliment to the last person who messaged. Make it specific.",
    "Give the group a harmless riddle. You must know the answer.",
    "Create a fake gaming patch note about yourself. Include one \"buff\" and one \"nerf.\"",
    "Describe your phone battery like a sports score. Make the commentary dramatic.",
    "Write a fake customer-support message to your own brain. Explain the problem.",
    "Send a message that sounds suspicious. Make the actual meaning completely harmless.",
    "Pretend you're a tour guide. Give a 3-sentence tour of your room.",
    "Send your next message in rhyme. At least two lines.",
    "Make a fake announcement for the group. Include an absurd but harmless rule.",
    "Send a message where each sentence gets shorter. Make three sentences.",
    "Give yourself a dramatic codename. Explain why you chose it.",
    "Write a tiny mystery. The answer must be something ordinary.",
    "Describe your favorite snack as a luxury car. Explain its \"features.\"",
    "Invent a harmless challenge for the group. It must take under five minutes.",
    "Pretend you're a game narrator. Narrate your next action dramatically.",
    "Create a fake product called \"Rumi Brain.\" Explain what it supposedly does.",
    "Describe an ordinary object as magical. Give it one imaginary power.",
    "Create a three-level ranking of your current mood. Name each level creatively.",
    "Write a fake patch update for your personality. Include one new feature.",
    "Send a message that contains exactly one emoji. Make the message funny.",
    "Pretend you're hosting a game show. Introduce the next contestant dramatically.",
    "Create a one-sentence prophecy for the group. Make it mysterious but harmless.",
    "Send a voice note saying a completely normal sentence dramatically. Pretend it's the final scene of a movie.",
    "Make a fake travel advertisement for your room. Include one \"must-see attraction.\"",
    "Invent a fictional restaurant with the weirdest name possible. Give it one signature dish.",
    "Send a message using only animal emojis. Let the group decode it.",
    "Make a fake breaking-news report about something happening around you. Keep it completely harmless.",
    "Give yourself a ridiculous professional title. Use it once in chat.",
    "Send a compliment using exactly seven words. Make it genuine.",
    "Describe your favorite food as if you're reviewing a movie. Give it a rating.",
    "Send three emojis and let someone choose what you must write about. Follow their choice for one message.",
    "Make a fake social-media post from your future self. Mention one harmless achievement.",
    "Write a mini comedy scene between you and your alarm clock. Keep it under four lines.",
    "Send a message where every sentence contains exactly five words. Write three sentences.",
    "Invent a ridiculous law for the entire internet. Explain why it exists.",
    "Make a fake \"wanted\" poster for a missing object. The object must be something harmless.",
    "Write a two-line poem about your phone battery. Make it painfully relatable.",
    "Invent a new game mode for Rumi. Explain the basic rule in two sentences.",
    "Pretend you're receiving an award. Give a three-sentence acceptance speech.",
    "Make up a completely harmless rumor about yourself. Immediately reveal that it's fake.",
    "Describe your favorite person to talk to using only positive words. Don't reveal anything private.",
    "Send a voice message saying \"The mission is complete.\" Then reveal a very ordinary mission.",
    "Create a fake job title for yourself. Explain your responsibilities.",
    "Send a voice note saying \"plot twist.\" Then continue with an ordinary sentence.",
    "Send a voice message saying one harmless tongue twister. Try to say it quickly.",
    "Send a voice note pretending you're giving a victory speech. Your victory can be about anything silly.",
    "Pick an object near you. Give it a ridiculous name.",
    "Send a motivational quote you completely made up. Make it sound extremely serious.",
    "Send a dramatic \"mission accepted\" message. Your mission can be making a sandwich.",
    "Send a message containing only food emojis. Let others guess what you mean.",
    "Invent a new slang word. Give its definition.",
    "Invent a completely useless invention. Explain what it does.",
    "Pretend your phone is giving you advice. Write its best advice.",
    "Write your next message like a Shakespearean character. Keep it under two sentences.",
    "Describe your favorite movie terribly. Let someone guess it.",
    "Send a message using only questions. Make the questions increasingly ridiculous.",
    "Give your current activity a movie title. Make it dramatic.",
    "Write a fake customer review of your own personality. Give yourself a rating.",
    "Describe a common object like it's a legendary artifact. Make it sound powerful.",
    "Describe yourself using only movie genres. Use at least four genres.",
    "Write a three-line poem about Monday. Make Monday sound like a villain.",
    "Invent a ridiculous group rule. Everyone gets to vote on it.",
    "Describe your favorite drink like a scientist. Use unnecessarily complicated language.",
    "Describe your day using only emojis. Let someone interpret it.",
    "Write a mini motivational speech about getting out of bed. Make it ridiculously epic.",
    "Make a fake warning label for yourself. Example: \"May randomly disappear into games.\"",
    "Send a message as if you're from the year 3026. Mention one harmless future invention.",
    "Write your next message without using \"I.\" Keep it natural.",
    "Describe your favorite place using only five words. Make every word count.",
    "Pretend you're announcing a football final. The match is between two random objects.",
    "Pretend you're an AI assistant. Answer the next message extremely formally.",
    "Send a voice note of you reciting the alphabet backwards as far as you can.",
    "Type your reply to this using only your elbow.",
    "Send a voice note singing Happy Birthday to yourself.",
    "Type a message entirely in uppercase and end every sentence with '!!!'",
    "Send a voice note doing your best impression of a cat.",
    "Change your name to 'Error 404' for the next hour.",
    "Type a 50-word story about a potato and send it.",
    "Change your Telegram bio to your favorite quote, but misattribute it to Shrek.",
    "Type 'I love pineapples on pizza' and defend it if anyone argues.",
    "Send a voice note whispering 'They are watching' and nothing else.",
    "Type the name of your favorite movie, but with the vowels removed.",
    "Send a voice note doing your best evil laugh.",
    "Change your name to 'Anonymous' for the next 12 hours.",
    "Type a message using only the middle autocomplete suggestion 15 times.",
    "Send a voice note pretending to order a burger at a drive-thru.",
    "Tag a user and send them a virtual hug.",
    "Type 'I am a potato' in a language you don't speak using Google Translate.",
    "Send a voice note trying to hit the highest note you can.",
    "Tag a random person and say 'You're my hero.'",
    "Change your name to your favorite food for the next hour.",
    "Send a voice note doing an impression of a famous cartoon character.",
    "Type 'I am a time traveler from 2050' and refuse to explain further.",
    "Send a voice note trying to rap the fastest verse you know.",
    "Type a message using only emojis to describe your favorite movie.",
    "Send a voice note pretending to be a news anchor reporting a mundane event.",
    "Type 'I am legally obligated to say I am a bot' and send it.",
    "Send a voice note trying to whistle.",
    "Change your name to a random color for the next hour.",
    "Type a haiku about your favorite snack.",
    "Send a voice note pretending to be a robot.",
    "Send a voice note doing a terrible impression of an accent.",
    "Type a message alternating uppercase and lowercase letters like tHiS.",
    "Send a voice note pretending to be a tour guide for your room.",
    "Type 'I am communicating telepathically' and send it.",
    "Send a voice note trying to sound as creepy as possible.",
    "Change your name to 'Mystery Guest' for the next hour.",
    "Send a voice note pretending to be an alien observing humans.",
    "Type 'I am not who you think I am' and send it.",
    "Send a voice note of you laughing uncontrollably.",
    "Type a message using only punctuation marks.",
    "Send a voice note pretending to be a radio DJ.",
    "Type 'I have seen the future' and send it.",
    "Change your name to your favorite animal for the next hour.",
    "Type a message that sounds like a fortune cookie.",
    "Send a voice note of you doing a dramatic reading of a random text.",
    "Tag the group admin and say 'I am the captain now.'",
    "Tag the group admin and say 'I am ready for my promotion.'",
    "Change your Telegram bio to 'Professional Procrastinator'.",
    "Send a voice note to the group singing the chorus of your favorite song.",
    "Change your Telegram bio to 'I eat raw onions for breakfast' for the next hour.",
    "Send a message in the chat using only emojis to describe your current mood, and let others guess.",
    "Type a full sentence with your eyes closed and send it without editing.",
    "Send a voice note barking like a dog for 5 seconds.",
    "Change your Telegram name to 'Local Clown' for the next 12 hours.",
    "Go to your music app, put it on shuffle, and send the name of the first song that plays. No cheating.",
    "Send a voice note trying to do your best evil villain laugh.",
    "Write a 3-line romantic poem about the bot and send it to the chat.",
    "Spam the chat with 10 different random stickers in a row.",
    "Send a voice message whispering a spooky ghost story for 30 seconds.",
    "Tag the group admin and demand a promotion, giving a ridiculous reason why you deserve it.",
    "Find a funny meme online and send it to the chat, claiming you made it.",
    "Type your name using only your nose and send it.",
    "Send a voice note of you doing an impression of a famous celebrity.",
    "Send a voice message of you counting backward from 20 as fast as you can.",
    "Describe your favorite movie plot badly in one sentence and let the group guess it.",
    "Send a voice note of you trying to beatbox.",
    "Send a voice note of you reading a random Wikipedia article in a dramatic news-anchor voice.",
    "Find the weirdest GIF on Telegram and send it to the group.",
    "Send a message saying 'I have a confession to make...' and then don't reply for 10 minutes.",
    "Tag the last person who spoke in the chat and tell them a terrible dad joke.",
    "Send a voice note pretending to order a pizza from a highly confused operator.",
    "Write 'I am the supreme ruler of this group' backwards and send it.",
    "Type out the alphabet as fast as you can in a single message.",
    "Tag a user and challenge them to complete the next Dare instead of you.",
    "Send a voice note making the sound of an aggressive race car.",
    "Type \"I have a confession\" and then wait 2 minutes before sending the next message. The next message must be \"I don't actually have a confession. I just wanted attention.\"",
    "Send a voice note of you saying \"I'm not saying I'm Batman, but I'm not not saying it\" in the most serious tone possible.",
    "Send a voice note pretending to be a GPS that is having an existential crisis. Say \"Turn left… or don't. Nothing matters. Recalculating… why are we here?\" for 30 seconds.",
    "Send a voice note sharing 3 things you are secretly proud of, like a villain revealing a master plan. End with an evil laugh.",
    "Send the chat a single sentence that starts with \"I've never told anyone this, but…\" and then make it something completely absurd (e.g., \"I once ate a whole onion like an apple\").",
    "Send a voice note of you saying \"I'm not locked in here with you — you're locked in here with me\" while doing a dramatic pause.",
    "Write a 5-line breakup letter to your favorite app. Read it aloud with intense emotion. Send the voice note.",
    "Send a single emoji that represents your current mood with no explanation.",
    "Pick a random object and give a 30-second eulogy for it as if it just died. Use the phrase \"gone too soon\" at least once.",
    "Send a voice note saying \"I'm not saying I'm a genius, but I'm not not saying it\" in a British accent.",
    "Send a voice note of you whispering \"Check your back\" and nothing else.",
    "Create a 5-line \"conspiracy theory\" about why your favorite food is actually a government experiment. Include fake evidence, a whistleblower name, and a dramatic conclusion.",
    "Send a message that says \"I have a secret\" and then send a random fact about penguins.",
    "Send a single word that describes your current vibe with no context.",
    "Write a 6-line dramatic monologue about the last time someone made you wait. Send it as a voice note with full emotion.",
    "Send a voice note of you saying \"I'm not saying I'm the problem, but I'm also not saying I'm not.\"",
    "Send a 60-second voice note TED Talk on why your most boring habit is secretly a form of meditation. Include two made-up statistics.",
    "Write a 5-line poem about your morning routine — but make it sound like a Greek epic. Read it aloud with intense drama.",
]


# ---- no-repeat decks (per chat, in memory) ----
_decks = {}


def _draw(chat_id, kind):
    src = TRUTHS if kind == "truth" else DARES
    key = (chat_id, kind)
    deck = _decks.get(key)
    if not deck:
        deck = list(range(len(src)))
        random.shuffle(deck)
        _decks[key] = deck
    return src[deck.pop()]


def _name(user):
    return html.escape(user.first_name or "Player")


# ---- cards ----
def _lobby(user):
    text = (
        "🎭 <b>TRUTH OR DARE</b>\n"
        f"<blockquote>👤 <b>Player:</b> {_name(user)}\n"
        "<i>The spotlight is on you. Will you confess a secret, or face a challenge?</i></blockquote>"
    )
    uid = user.id
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("😇 Truth", callback_data=f"td:truth:{uid}"),
        InlineKeyboardButton("😈 Dare", callback_data=f"td:dare:{uid}"),
    ]])
    return text, kb


def _card(user, kind, item):
    uid = user.id
    if kind == "truth":
        text = (
            "😇 <b>TRUTH REVEALED</b>\n"
            f"<blockquote>👤 <b>Player:</b> {_name(user)}\n"
            f"💬 <b>Question:</b> <i>{html.escape(item)}</i></blockquote>\n"
            "<i>Answer honestly, no dodging! ✨</i>"
        )
        other = InlineKeyboardButton("😈 Switch to Dare", callback_data=f"td:dare:{uid}")
        again = InlineKeyboardButton("🔄 Another Truth", callback_data=f"td:truth:{uid}")
    else:
        text = (
            "😈 <b>CHALLENGE ACCEPTED</b>\n"
            f"<blockquote>👤 <b>Player:</b> {_name(user)}\n"
            f"🎯 <b>Dare:</b> <i>{html.escape(item)}</i></blockquote>\n"
            "<i>Do it and send proof in the chat! 🔥</i>"
        )
        other = InlineKeyboardButton("😇 Switch to Truth", callback_data=f"td:truth:{uid}")
        again = InlineKeyboardButton("🔄 Another Dare", callback_data=f"td:dare:{uid}")
    kb = InlineKeyboardMarkup([
        [again, other],
        [InlineKeyboardButton("🎭 Play Again", callback_data=f"td:again:{uid}")],
    ])
    return text, kb


# ---- handlers ----
async def td_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user = update.effective_message, update.effective_user
    if not msg or not user:
        return
    text, kb = _lobby(user)
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _direct(update: Update, kind):
    msg, user = update.effective_message, update.effective_user
    if not msg or not user:
        return
    text, kb = _card(user, kind, _draw(update.effective_chat.id, kind))
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def truth_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _direct(update, "truth")


async def dare_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _direct(update, "dare")


async def td_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.data:
        return
    parts = q.data.split(":")
    if len(parts) != 3 or parts[1] not in ("truth", "dare", "again"):
        await q.answer()
        return
    action = parts[1]
    try:
        owner = int(parts[2])
    except ValueError:
        await q.answer()
        return
    if q.from_user.id != owner:
        await q.answer("This spotlight belongs to someone else. Send /td for your own turn! 🎭", show_alert=True)
        return
    if action == "again":
        text, kb = _lobby(q.from_user)
    else:
        text, kb = _card(q.from_user, action, _draw(q.message.chat_id, action))
    await q.answer()
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass


def register(application):
    application.add_handler(CommandHandler("td", td_cmd))
    application.add_handler(CommandHandler("truth", truth_cmd))
    application.add_handler(CommandHandler("dare", dare_cmd))
    application.add_handler(CallbackQueryHandler(td_callback, pattern=r"^td:"))

