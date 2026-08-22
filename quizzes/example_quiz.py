"""Copy this file, edit it, run it:  uv run quizzes/my_quiz.py"""

from lykkepoller import FreeText, MultipleChoice, Quiz, Rating

quiz = Quiz(
    title="Demo polling session",
    theme="notebook",  # plain, teal, editorial, dark, notebook
    questions=[
        MultipleChoice(
            "Why do we really need the loss function?",
            options=[
                "To evaluate the model",
                "To give feedback to the model",
                "To initialize the model",
            ],
            correct="To give feedback to the model",
        ),
        FreeText("Where do you live?"),
        MultipleChoice("Pick a colour", options=["Red", "Blue", "Green"]),
        Rating(
            "How well did you follow this section?",
            low="Lost",
            high="Got it",
            steps=5,
        ),
    ],
)

if __name__ == "__main__":
    quiz.run()
