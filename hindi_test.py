import quote_pil as q
m = {"name": "नीलेश", "user_id": 5,
     "text": "नमस्ते दोस्तों, यह बॉट बहुत अच्छा है!",
     "entities": [], "avatar": None, "tag": None, "reply": None}
q.render_messages([m]).save("test_quotes/08_hindi.png")
print("saved")
