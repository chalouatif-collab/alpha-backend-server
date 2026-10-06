require('dotenv').config();
const express = require('express');

const app = express();
app.use(express.json());

const VERIFY_TOKEN = process.env.VERIFY_TOKEN;

// مسار الويب هوك للتحقق من فيسبوك
app.get('/webhook', (req, res) => {
  const mode = req.query['hub.mode'];
  const token = req.query['hub.verify_token'];
  const challenge = req.query['hub.challenge'];

  if (mode && token) {
    if (mode === 'subscribe' && token === VERIFY_TOKEN) {
      console.log('تم التحقق من الويب هوك بنجاح!');
      res.status(200).send(challenge);
    } else {
      res.sendStatus(403);
    }
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`الخادم يعمل الآن على المنفذ ${PORT}`);
});