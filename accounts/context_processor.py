from .forms import AccountRegistrationForm


def authentication_forms(request):
    return {
        'register_form': AccountRegistrationForm(),
    }
