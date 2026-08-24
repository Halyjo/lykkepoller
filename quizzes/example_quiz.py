"""Copy this file, edit it, run it:  uv run quizzes/my_quiz.py"""

from lykkepoller import FreeText, MultipleChoice, Quiz, Rating

quiz = Quiz(
    title="Demo polling session",
    theme="notebook",  # plain, teal, editorial, dark, notebook
    questions=[
        MultipleChoice(
            prompt="Where does learning happen?",
            options=[
                "On the screen",
                "In the learners head",
                "On a blackboard",
                "In a dialogue",
            ],
            correct="In the learners head",
        ),
        MultipleChoice(
            "Why do we really need the loss function?",
            options=[
                "To evaluate the model",
                "To give feedback to the model",
                "To initialize the model",
            ],
            correct="To give feedback to the model",
        ),
        FreeText("What is your favorite food?"),
        MultipleChoice(
            "Pick a colour", 
            options=["Red", "Blue", "Green"],
            correct=["Red", "Blue", "Green"],
        ),
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
